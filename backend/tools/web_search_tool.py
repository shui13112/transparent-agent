from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from langchain_core.documents import Document

from config import get_settings
from memory.session_context import current_session_id
from prompt.query_processing import generate_sub_queries, get_search_results
from retrievers.retriever import get_retrievers
from scraper.scraper import Scraper
from tools.crawler_util import get_user_agent
from utils.workers import WorkerPool
from utils.compression import KnowledgeBaseManager as _KBM
from utils.web_cache_db import WebCacheDB
import logging

class WebSearchInput(BaseModel):
    question: str = Field(
        ...,
        description="需要搜索的问题。可以是用户提问的规范化表达，也可以是你认为需要搜索的问题。",
    )


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "在网络上搜索信息。用户每次提问尽量只使用该工具一次"
        "工具内部会自动进行问题重构：先做初步搜索获取实时上下文，"
        "再用轻量大模型将原始问题拆解为多个可独立搜索的子问题，最后逐一搜索。"
        "输出是从网络上搜集到的最相关信息的片段。"
    )
    args_schema: Type[BaseModel] = WebSearchInput

    def __init__(self, base_dir: Path, kb_manager: _KBM, **kwargs) -> None:
        super().__init__(**kwargs)
        settings = get_settings()
        self._base_dir = base_dir
        self._retrievers = get_retrievers(settings.retrievers)
        self._kb_manager = kb_manager
        self._worker_pool = WorkerPool(
            max_workers=settings.max_concurrency_num,
            rate_limit_delay=settings.rate_limit_delay,
            request_delay=settings.request_delay,
        )
        self._scraper_type = "bs"
        self._user_agent = get_user_agent()
        self._web_cache_dir = str(base_dir / "chroma_db" / "sessions")
        self._max_retrieved_urls = settings.max_retrieved_urls
        self._max_doc_chars = settings.component_char_limit
        self._proxy_provider = None
        if settings.enable_proxy:
            from proxy.free_proxy_provider import FreeProxyProvider
            self._proxy_provider = FreeProxyProvider(pool_size=settings.proxy_pool_size)
        self._cookie_manager = None
        if settings.cookie_persistence:
            from scraper.cookie_manager import CookieManager
            self._cookie_manager = CookieManager()

    def _run(
        self,
        question: str,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(self._arun(question))
        else:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, self._arun(question)).result()
        status = getattr(self, '_status_msg', None)
        if status:
            from tools import current_tool_status
            current_tool_status.set({"status_msg": status})
        return result

    async def _arun(
        self,
        question: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        # 步骤 0：问题重构 — 先做初步搜索获取上下文，再用轻量模型拆解子问题
        sub_queries = await generate_sub_queries(question)

        retriever_cls = self._retrievers[0]

        # 用配置的检索器对每个子查询并发搜索
        all_search_results: list[dict] = []
        tasks = [get_search_results(sq, retriever_cls) for sq in sub_queries]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        for results in results_list:
            if isinstance(results, list):
                all_search_results.extend(results)

        if not all_search_results:
            return "Web search returned no results."

       # 3. 提取 URL 并流式抓取。抓取阶段只缓存和收集文档，避免每个网页重复触发向量库写入。
        seen_urls: set[str] = set()
        urls: list[str] = []
        for result in all_search_results:
            href = result.get("href")
            if not href or len(href) > 200 or href in seen_urls:
                continue
            seen_urls.add(href)
            urls.append(href)
            if len(urls) >= self._max_retrieved_urls:
                break

        self._status_msg = (
            f"用{', '.join(sub_queries)} 共计 {len(sub_queries)} 个子问题检索了 {len(urls)} 个网页"
        )
        scraper = Scraper(urls, self._user_agent, self._scraper_type, self._worker_pool, url_timeout=45, proxy_provider=self._proxy_provider, cookie_manager=self._cookie_manager)

        sid = current_session_id.get()
        self._kb_manager.get_session_client(sid)  # 确保 chroma_db/sessions/{sid}/ 目录存在
        cache_db = WebCacheDB(f"{self._web_cache_dir}/{sid}/web_cache.db")
        docs: list[Document] = []

        try:
            async for result in scraper.run_streaming():
                raw = result.get("raw_content", "")
                url = result.get("url", "")
                title = result.get("title", "")

                if not raw or len(raw) < 100:
                    continue

                # 立即存入 SQLite 缓存
                cache_db.upsert(url=url, title=title, content=raw)

                # 回答前仍会做向量检索；这里先收集文档，抓取完成后批量入库。
                docs.append(Document(
                    page_content=raw[:self._max_doc_chars],
                    metadata={"source": url, "title": title},
                ))
        finally:
            await scraper.save_all_cookies()

        valid_count = len(docs)
        if valid_count == 0:
            return "Scraping failed for all URLs."

        # 4. 批量写入动态向量库，再用每个子问题各检索 top-5，去重合并。
        index = self._kb_manager.build_dynamic_index_from_docs(docs, session_id=sid)
        logging.info('向量化完成')
        retriever = index.as_retriever(similarity_top_k=5)
        seen: set[tuple[str, str]] = set()
        merged_nodes: list = []
        for sq in sub_queries:
            for item in retriever.retrieve(sq):
                node = item.node if hasattr(item, "node") else item
                text = node.get_content() if hasattr(node, "get_content") else str(node)
                url = node.metadata.get("source", "unknown")
                key = (url, text[:100])
                if key not in seen:
                    seen.add(key)
                    merged_nodes.append((url, text))
        logging.info('检索完成')
        if not merged_nodes:
            return f"Web search indexed {valid_count} pages but no relevant passages found for the query."

        lines = [f"Retrieved {len(merged_nodes)} relevant passages:"]
        for i, (url, text) in enumerate(merged_nodes, 1):
            lines.append(f"\n[{i}] source: {url}\n{text[:800]}")
        logging.info('完成搜索')
        return "\n".join(lines)
