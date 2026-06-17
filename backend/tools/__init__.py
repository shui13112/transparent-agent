from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools import BaseTool


from tools.get_full_information_tool import GetFullInformationTool
from tools.rag_tool import RAGTool
from tools.read_file_tool import ReadFileTool
from tools.web_search_tool import WebSearchTool
from utils.compression import KnowledgeBaseManager

current_tool_status: ContextVar[dict | None] = ContextVar("current_tool_status", default=None)


def get_all_tools(base_dir: Path, kb_manager: KnowledgeBaseManager, rag_mode: bool = False) -> list[BaseTool]:
    tools: list[BaseTool] = [
        ReadFileTool(root_dir=base_dir),
        WebSearchTool(base_dir=base_dir, kb_manager=kb_manager),
        GetFullInformationTool(base_dir=base_dir),
    ]
    if rag_mode:
        tools.append(RAGTool(base_dir=base_dir, kb_manager=kb_manager))
    return tools
