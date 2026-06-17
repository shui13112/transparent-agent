from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from langchain.agents import create_agent
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

try:
    from langchain_deepseek import ChatDeepSeek
except ImportError:  # pragma: no cover - optional dependency at runtime
    ChatDeepSeek = None

from config import get_settings, runtime_config
from memory.session_context import current_session_id
from memory.session_manager import SessionManager
from prompt.prompt_builder import build_system_prompt
from tools import get_all_tools, current_tool_status
from utils.compression import KnowledgeBaseManager

_TOOL_LABELS = {
    "web_search": "网络搜索",
    "web_search_refined": "精确搜索",
    "get_full_information": "获取详情",
    "read_file": "读取文件",
    "rag_search": "知识库检索",
}


class _ChatDeepSeekReasoning(ChatDeepSeek if ChatDeepSeek else object):
    """ChatDeepSeek 子类，确保 reasoning_content 在工具调用后被正确回传。

    DeepSeek API 要求：在进行了工具调用的轮次后，后续所有请求必须完整回传
    reasoning_content。LangChain 默认的消息序列化（_convert_message_to_dict）
    不包含此字段，因此在此子类中补齐。
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        lc_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        for i, lc_msg in enumerate(lc_messages):
            if isinstance(lc_msg, AIMessage):
                reasoning = lc_msg.additional_kwargs.get("reasoning_content")
                if reasoning and i < len(payload.get("messages", [])):
                    payload["messages"][i]["reasoning_content"] = reasoning

        _log_llm_request(payload)
        return payload


def _log_llm_request(payload: dict) -> None:
    """输出发送给大模型的 JSON 请求体，便于调试格式问题。"""
    model = payload.get("model", "?")
    messages_snapshot: list[dict] = []
    for m in payload.get("messages", []):
        content = m.get("content", "")
        if isinstance(content, str) and len(content) > 300:
            content = content[:300] + "…[已截断]"
        msg_snap = {"role": m.get("role"), "content": content}
        if "tool_calls" in m:
            msg_snap["tool_calls"] = [
                {"name": tc.get("function", {}).get("name", "?"), "id": tc.get("id", "?")}
                for tc in m["tool_calls"]
            ]
        if "tool_call_id" in m:
            msg_snap["tool_call_id"] = m["tool_call_id"]
        if "reasoning_content" in m:
            r = m["reasoning_content"]
            msg_snap["reasoning_content"] = r[:200] + "…" if len(r) > 200 else r
        messages_snapshot.append(msg_snap)

    log_data: dict = {
        "model": model,
        "stream": payload.get("stream"),
        "temperature": payload.get("temperature"),
        "tools_count": len(payload.get("tools", [])),
        "messages": messages_snapshot,
    }

    logger.info("LLM request to %s: %s", model, json.dumps(log_data, ensure_ascii=False))


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


def _tool_result_success(output: str) -> bool:
    """判断工具调用是否成功。空结果或包含明确错误标记视为失败。"""
    if not output or not output.strip():
        return False
    lower = output.strip().lower()
    error_markers = ["error:", "traceback", "exception:", "failed:", "请求失败", "执行失败"]
    return not any(marker in lower for marker in error_markers)


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self._kb_manager: KnowledgeBaseManager | None = None

    def initialize(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self._kb_manager = KnowledgeBaseManager(persist_dir=str(base_dir / "chroma_db"))

    def _build_chat_model(self):
        settings = get_settings()

        if (settings.smart_llm_model or "").startswith("deepseek"):
            if ChatDeepSeek is None:
                raise RuntimeError("langchain-deepseek is not installed")
            if not settings.smart_llm_api_key:
                raise RuntimeError("Missing API key for provider deepseek")
            return _ChatDeepSeekReasoning(
                model=settings.smart_llm_model,
                api_key=settings.smart_llm_api_key,
                api_base=settings.smart_llm_base_url,
                temperature=0,
            )

        if not settings.smart_llm_api_key:
            raise RuntimeError(f"Missing API key for provider {settings.smart_llm_model}")

        return ChatOpenAI(
            model=settings.smart_llm_model,
            api_key=settings.smart_llm_api_key,
            base_url=settings.smart_llm_base_url,
            temperature=0,
        )

    def _build_agent(
        self,
        history: list[dict[str, Any]] | None = None,
        knowledge_context: str = "",
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        rag_mode = runtime_config.get_rag_mode()
        system_prompt = build_system_prompt(
            self.base_dir,
            rag_mode=rag_mode,
            history=history,
            knowledge_context=knowledge_context,
        )
        return create_agent(
            model=self._build_chat_model(),
            tools=get_all_tools(self.base_dir, self._kb_manager, rag_mode=rag_mode),
            system_prompt=system_prompt,
        )

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
        knowledge_context: str = "",
        session_id: str = "",
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        token = current_session_id.set(session_id)
        try:
            agent = self._build_agent(history=history, knowledge_context=knowledge_context)
            # 会话历史已通过系统提示词注入，此处仅传当前用户消息
            messages = [{"role": "user", "content": message}]

            final_content_parts: list[str] = []
            last_ai_message = ""
            pending_tools: dict[str, dict[str, str]] = {}

            async for mode, payload in agent.astream(
                {"messages": messages},
                stream_mode=["messages", "updates"],
            ):
                try:
                    if mode == "messages":
                        chunk, _metadata = payload
                        if getattr(chunk, "type", "") == "tool":
                            continue
                        text = _stringify_content(getattr(chunk, "content", ""))
                        if text:
                            final_content_parts.append(text)
                            yield {"type": "token", "content": text}
                        continue

                    if mode != "updates":
                        continue

                    for update in payload.values():
                        for agent_message in update.get("messages", []):
                            message_type = getattr(agent_message, "type", "")
                            tool_calls = getattr(agent_message, "tool_calls", []) or []

                            if message_type == "ai" and not tool_calls:
                                candidate = _stringify_content(getattr(agent_message, "content", ""))
                                if candidate:
                                    last_ai_message = candidate

                            if tool_calls:
                                for tool_call in tool_calls:
                                    call_id = str(tool_call.get("id") or tool_call.get("name"))
                                    tool_name = str(tool_call.get("name", "tool"))
                                    tool_args = tool_call.get("args", "")
                                    if not isinstance(tool_args, str):
                                        tool_args = json.dumps(tool_args, ensure_ascii=False)
                                    pending_tools[call_id] = {
                                        "tool": tool_name,
                                        "input": str(tool_args),
                                    }
                                    yield {
                                        "type": "tool_start",
                                        "tool": tool_name,
                                        "input": str(tool_args),
                                    }

                            if message_type == "tool":
                                tool_call_id = str(getattr(agent_message, "tool_call_id", ""))
                                pending = pending_tools.pop(
                                    tool_call_id,
                                    {"tool": getattr(agent_message, "name", "tool"), "input": ""},
                                )
                                output = _stringify_content(getattr(agent_message, "content", ""))
                                success = _tool_result_success(output)
                                yield {
                                    "type": "tool_end",
                                    "tool": pending["tool"],
                                    "success": success,
                                }
                                if success:
                                    ts = current_tool_status.get()
                                    if ts:
                                        yield {"type": "tool_status", "status_msg": ts["status_msg"]}
                                    else:
                                        label = _TOOL_LABELS.get(pending["tool"], pending["tool"])
                                        yield {"type": "tool_status", "status_msg": f"{label}执行成功"}
                                    current_tool_status.set(None)
                                yield {"type": "new_response"}
                except Exception as exc:
                    logger.exception("Agent stream iteration error: %s", exc)
                    yield {
                        "type": "error",
                        "code": "agent_stream_error",
                        "message": "Agent stream iteration failed.",
                    }
                    break

            final_content = "".join(final_content_parts).strip() or last_ai_message.strip()
            yield {"type": "done", "content": final_content}
        finally:
            current_session_id.reset(token)

    async def generate_title(self, first_user_message: str) -> str:
        prompt = (
            "请根据用户的第一条消息生成一个中文会话标题。"
            "要求不超过 10 个汉字，不要带引号，不要解释。"
        )   
        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": first_user_message},
                ]
            )
            title = _stringify_content(getattr(response, "content", "")).strip()
            return title[:10] or "新会话"
        except Exception:
            return (first_user_message.strip() or "新会话")[:10]

    async def summarize_history(self, messages: list[dict[str, Any]]) -> str:
        prompt = (
            "请将以下对话压缩成中文摘要，控制在 500 字以内。"
            "重点保留用户目标、已完成步骤、重要结论和未解决事项。"
        )
        lines: list[str] = []
        for item in messages:
            role = item.get("role", "assistant")
            content = str(item.get("content", "") or "")
            if content:
                lines.append(f"{role}: {content}")
        transcript = "\n".join(lines)

        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ]
            )
            summary = _stringify_content(getattr(response, "content", "")).strip()
            return summary[:500]
        except Exception:
            return transcript[:500]


agent_manager = AgentManager()
