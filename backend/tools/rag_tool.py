from __future__ import annotations

from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from config import get_settings
from memory.session_context import current_session_id
from utils.compression import KnowledgeBaseManager as _KBM
from utils.reranker import Reranker


# 初筛召回数量（第一阶段：向量检索）
_INITIAL_TOP_K = 20


class RAGInput(BaseModel):
    query: str = Field(..., description="要查询的问题或关键词")
    source: str = Field(
        default="static",
        description="知识库来源: 'static' 查询静态文件向量库(PDF/TXT), 'web' 查询网页数据向量库",
        pattern=r"^(static|web)$",
    )
    top_k: int = Field(default=5, ge=1, le=20, description="返回的最相关片段数量（rerank 后取前 K 个）")


class RAGTool(BaseTool):
    name: str = "rag_search"
    description: str = (
        "通过 RAG 检索增强方式查询知识库。"
        "默认查询静态文件向量数据库（PDF/TXT 文档），"
        "也可通过 source='web' 查询网页抓取数据的向量数据库。"
        "适用于需要从已索引的本地文档或历史网页内容中获取信息的场景。"
    )
    args_schema: Type[BaseModel] = RAGInput

    def __init__(self, base_dir: Path, kb_manager: _KBM, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir
        self._kb = kb_manager

    def _get_index(self, source: str):
        if source == "web":
            return self._kb.get_dynamic_index(current_session_id.get())
        return self._kb.build_static_index()

    def _run(
        self,
        query: str,
        source: str = "static",
        top_k: int = 5,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        try:
            index = self._get_index(source)
            if index is None:
                return "RAG 检索不可用：未配置 Embedding 模型，请设置 EMBEDDING_MODEL 环境变量。"
            # 第一阶段：向量检索召回 top-20
            retriever = index.as_retriever(similarity_top_k=_INITIAL_TOP_K)
            nodes = retriever.retrieve(query)
        except Exception as e:
            return f"RAG 检索失败: {e}"

        if not nodes:
            source_label = "静态文档库" if source == "static" else "网页数据库"
            return f"在{source_label}中未找到相关内容。"

        # 提取每个 node 的文本和元数据
        node_data = []
        for item in nodes:
            node = item.node if hasattr(item, "node") else item
            text = node.get_content() if hasattr(node, "get_content") else str(node)
            score = getattr(item, "score", 0.0) or 0.0
            url = node.metadata.get("source", "")
            file_name = node.metadata.get("file_name", "")
            node_data.append({"text": text, "score": score, "url": url, "file_name": file_name})

        # 第二阶段：cross-encoder rerank
        chunks = [d["text"] for d in node_data]
        settings = get_settings()
        reranker = Reranker.get_instance(settings.reranker_model)
        ranked = reranker.rerank(query, chunks, top_k=top_k)

        lines = [f"向量检索召回 {len(nodes)} 个片段，Rerank 后取前 {len(ranked)} 个:"]
        for new_i, (orig_idx, rerank_score) in enumerate(ranked, 1):
            d = node_data[orig_idx]
            source_info = d["url"] or d["file_name"] or "未知来源"
            vector_score = d["score"]
            lines.append(
                f"\n[{new_i}] 来源: {source_info}"
                f"  (向量分: {vector_score:.3f}, Rerank分: {rerank_score:.3f})"
                f"\n{d['text'][:1000]}"
            )

        return "\n".join(lines)

    async def _arun(
        self,
        query: str,
        source: str = "static",
        top_k: int = 5,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        import asyncio
        return await asyncio.to_thread(self._run, query, source, top_k, None)
