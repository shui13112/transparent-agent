from __future__ import annotations

from typing import Type
from urllib.parse import urlparse

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun
from langchain_core.documents import Document
from pydantic import BaseModel

from prompt.query_processing import get_search_results
from retrievers.retriever import get_retriever
from scraper.scraper import Scraper
from tools.web_search_tool import WebSearchInput, WebSearchTool
from utils.web_cache_db import WebCacheDB
from memory.session_context import current_session_id


class WebSearchRefinedTool(WebSearchTool):
    """精确搜索工具：直接用问题搜索，不做子问题拆解。

    适合用户对上一轮回答中的某个具体点进行追问或深入搜索的场景，
    避免不必要的 LLM 调用和问题重构。
    """

    name: str = "web_search_refined"
    description: str = (
        "精确搜索工具：直接以用户问题在网络上搜索，不做任何问题拆解或重构。"
        "适合对某个具体点做深入搜索，或用户对上一轮搜索结果中的特定细节进行追问时使用。"
    )
    args_schema: Type[BaseModel] = WebSearchInput

    async def _arun(
        self,
        question: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        per_count = self._urls_per_retriever

        # 步骤 1：直接用问题搜索，tavily → duckduckgo 降级
        url_body: dict[str, str] = {}
        url_domain: dict[str, str] = {}

        tavily_cls = get_retriever("tavily")
        if tavily_cls:
            results = await get_search_results(question, tavily_cls, max_results=per_count)
            valid = self._filter_valid(results)
        else:
            valid = []

        if not valid:
            ddg_cls = get_retriever("duckduckgo")
            if ddg_cls:
                results = await get_search_results(question, ddg_cls, max_results=per_count)
                valid = self._filter_valid(results)

        for r in valid:
            href = r["href"]
            if href not in url_body:
                url_body[href] = r.get("body", "")
                url_domain[href] = urlparse(href).netloc

        if not url_body:
            return "Web search returned no results."

        # 步骤 2：域名多样性截断
        urls: list[str] = []
        seen_domains: set[str] = set()
        for url in url_body:
            if len(urls) >= self._max_retrieved_urls:
                break
            domain = url_domain.get(url, "")
            if domain and domain in seen_domains and len(urls) >= self._max_retrieved_urls // 2:
                continue
            urls.append(url)
            seen_domains.add(domain)

        self._status_msg = f"精确搜索，检索了 {len(urls)} 个网页"

        # 步骤 3：并发抓取
        scraper = Scraper(
            urls, self._user_agent, self._scraper_type, self._worker_pool,
            url_timeout=45, proxy_provider=self._proxy_provider, cookie_manager=self._cookie_manager,
        )

        sid = current_session_id.get() or "__no_session__"
        self._kb_manager.get_session_client(sid)
        cache_db = WebCacheDB(f"{self._web_cache_dir}/{sid}/web_cache.db")
        docs: list[Document] = []

        try:
            async for result in scraper.run_streaming():
                raw = result.get("raw_content", "")
                url = result.get("url", "")
                title = result.get("title", "")

                if not raw or len(raw) < 100:
                    continue

                cache_db.upsert(url=url, title=title, content=raw)

                docs.append(Document(
                    page_content=raw[:self._max_doc_chars],
                    metadata={"source": url, "title": title},
                ))
        finally:
            await scraper.save_all_cookies()

        valid_count = len(docs)
        if valid_count == 0:
            return "Scraping failed for all URLs."

        # 步骤 4：向量入库 + 对原问题检索 top-5
        index = self._kb_manager.build_dynamic_index_from_docs(docs, session_id=sid)
        retriever = index.as_retriever(similarity_top_k=5)
        merged_nodes: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in retriever.retrieve(question):
            node = item.node if hasattr(item, "node") else item
            text = node.get_content() if hasattr(node, "get_content") else str(node)
            url = node.metadata.get("source", "unknown")
            key = (url, text[:100])
            if key not in seen:
                seen.add(key)
                merged_nodes.append((url, text))

        if not merged_nodes:
            return f"Web search indexed {valid_count} pages but no relevant passages found."

        # 步骤 5：格式化输出
        lines = [f"Retrieved {len(merged_nodes)} relevant passages:"]
        for i, (url, text) in enumerate(merged_nodes, 1):
            lines.append(f"\n[{i}] source: {url}\n{text[:800]}")
        return "\n".join(lines)
