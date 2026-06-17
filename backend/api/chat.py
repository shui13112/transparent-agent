from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from graph.agent import agent_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str
    stream: bool = True


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_segment() -> dict[str, Any]:
    return {"content": "", "tool_calls": []}


def _error_payload(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _parse_sse(raw_event: str) -> tuple[str, dict[str, Any]]:
    event_type = ""
    data: dict[str, Any] = {}
    for line in raw_event.splitlines():
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            try:
                parsed = json.loads(line.removeprefix("data: "))
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                data = parsed
    return event_type, data


@router.post("/chat")
async def chat(payload: ChatRequest):
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    history_record = session_manager.load_session_record(payload.session_id)
    history = session_manager.load_session_for_agent(payload.session_id)
    is_first_user_message = not any(
        message.get("role") == "user"
        for message in history_record.get("messages", [])
    )

    async def event_generator():
        segments: list[dict[str, Any]] = []
        current_segment = _new_segment()

        try:
            async for event in agent_manager.astream(payload.message, history, session_id=payload.session_id):
                event_type = event["type"]

                if event_type == "token":
                    current_segment["content"] += event.get("content", "")
                elif event_type == "tool_start":
                    current_segment["tool_calls"].append(
                        {
                            "tool": event.get("tool", "tool"),
                            "input": event.get("input", ""),
                            "output": "",
                        }
                    )
                elif event_type == "tool_end":
                    if current_segment["tool_calls"]:
                        current_segment["tool_calls"][-1]["success"] = event.get("success", False)
                elif event_type == "tool_status":
                    current_segment["tool_status"] = event.get("status_msg", "")
                elif event_type == "new_response":
                    if current_segment["content"].strip() or current_segment["tool_calls"]:
                        segments.append(current_segment)
                    current_segment = _new_segment()
                elif event_type == "done":
                    if not current_segment["content"].strip() and event.get("content"):
                        current_segment["content"] = event["content"]
                    if current_segment["content"].strip() or current_segment["tool_calls"]:
                        segments.append(current_segment)

                    session_manager.save_message(payload.session_id, "user", payload.message)
                    for segment in segments:
                        session_manager.save_message(
                            payload.session_id,
                            "assistant",
                            segment["content"],
                            tool_calls=segment["tool_calls"] or None,
                            tool_status=segment.get("tool_status"),
                        )
                elif event_type == "error":
                    event = {
                        "type": "error",
                        **_error_payload(
                            str(event.get("code") or "agent_error"),
                            str(event.get("message") or "Agent processing failed."),
                        ),
                    }

                data = {key: value for key, value in event.items() if key != "type"}
                yield _sse(event_type, data)

                if event_type == "done" and is_first_user_message:
                    title = await agent_manager.generate_title(payload.message)
                    session_manager.set_title(payload.session_id, title)
                    yield _sse(
                        "title",
                        {"session_id": payload.session_id, "title": title},
                    )
        except Exception as exc:
            logger.exception("Chat request failed: %s", exc)
            session_manager.save_message(payload.session_id, "user", payload.message)
            if current_segment["content"].strip() or current_segment["tool_calls"]:
                segments.append(current_segment)
            for segment in segments:
                session_manager.save_message(
                    payload.session_id,
                    "assistant",
                    segment["content"],
                    tool_calls=segment["tool_calls"] or None,
                    tool_status=segment.get("tool_status"),
                )
            yield _sse(
                "error",
                _error_payload(
                    "chat_request_failed",
                    "请求处理失败，请稍后重试或查看服务日志。",
                ),
            )

    if payload.stream:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    final_text = ""
    title = None
    error = None
    async for raw_event in event_generator():
        event_type, data = _parse_sse(raw_event)
        if event_type == "done":
            final_text = str(data.get("content", ""))
        elif event_type == "title":
            title = data.get("title")
        elif event_type == "error":
            error = data

    payload: dict[str, Any] = {"content": final_text}
    if title:
        payload["title"] = title
    if error:
        payload["error"] = error
        if not final_text:
            return JSONResponse(payload, status_code=500)
    return JSONResponse(payload)
