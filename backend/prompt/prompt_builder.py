"""
系统提示词构建器 — 所有上下文注入的统一入口。

每次收到用户输入时，此模块负责将以下信息组装为完整的系统提示词：
- 角色身份
- 可用技能列表
- 问题重构协议（拆解子问题）
- 会话历史
- 知识库检索结果
- 行事准则与输出规范
- RAG 模式提示
- 工作环境信息
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .build_default_prompt import (
    build_identity,
    build_code_of_conduct,
    build_output_format,
    build_rag_section,
)



# --- 技能快照 ---

def _load_skills_snapshot(base_dir: Path) -> str:
    snapshot_path = base_dir / "SKILLS_SNAPSHOT.md"
    if snapshot_path.exists():
        content = snapshot_path.read_text(encoding="utf-8").strip()
        if content:
            return f"## 可用技能\n\n{content}"
    return ""




# --- 会话历史 ---

def _format_history(history: list[dict]) -> str:
    if not history:
        return ""

    lines = ["## 会话历史", ""]
    for i, item in enumerate(history, 1):
        role = item.get("role", "unknown")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        # 截断过长的历史消息，防止 prompt 膨胀
        truncated = content[:2000]
        if len(content) > 2000:
            truncated += "…[已截断]"
        lines.append(f"### [{i}] {label}\n{truncated}\n")

    if len(lines) == 2:
        return ""
    return "\n".join(lines)


# --- 知识库上下文 ---

def _format_knowledge(knowledge_context: str) -> str:
    if not knowledge_context.strip():
        return ""
    return f"## 知识库检索结果\n\n以下是从本地知识库中检索到的相关内容，可作为回答的参考依据：\n\n{knowledge_context.strip()}"


# --- 主入口 ---

def build_system_prompt(
    base_dir: Path,
    rag_mode: bool = False,
    history: list[dict] | None = None,
    knowledge_context: str = "",
) -> str:
    """构建完整的 agent 系统提示词。

    所有上下文注入在此统一完成，调用方只需传入数据即可。

    Args:
        base_dir: 项目后端目录。
        rag_mode: 是否启用 RAG 检索增强。
        history: 会话历史消息列表。
        knowledge_context: 预先检索到的知识库内容（可为空）。

    Returns:
        完整的系统提示词字符串。
    """
    sections: list[str] = []

    # 1. 角色身份
    sections.append(build_identity())

    # 2. 可用技能列表
    skills = _load_skills_snapshot(base_dir)
    if skills:
        sections.append(skills)


    # 3. 知识库检索结果（如果有）
    if knowledge_context.strip():
        sections.append(_format_knowledge(knowledge_context))

    # 4. 会话历史（如果有）
    if history:
        formatted = _format_history(history)
        if formatted:
            sections.append(formatted)

    # 5. 行事准则
    sections.append(build_code_of_conduct())

    # 6. 输出规范
    sections.append(build_output_format())

    # 7. RAG 检索增强提示
    if rag_mode:
        sections.append(build_rag_section())



    return "\n\n".join(sections)
