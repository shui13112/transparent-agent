"""获取回答来源的完整原始文本。

当用户指出上一轮回答中某一点与原文有出入或者大模型认为需要完整信息时，agent 调用此工具，
根据回答中标注的来源 URL 或文件路径，返回该来源的完整内容以便逐字核对。

本地文件仅读取 pdf / txt；网页通过 Scraper.get_scraper 确定抓取器后抓取。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Type

import requests
from langchain_core.callbacks.manager import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from scraper.pymupdf.pymupdf import PyMuPDFScraper
from scraper.scraper import Scraper
from tools.crawler_util import get_user_agent, build_browser_headers
from config import get_settings
from utils.workers import WorkerPool


class GetFullInformationInput(BaseModel):
    sources: list[str] = Field(
        ...,
        description=(
            "需要获取完整内容的来源列表，每项为一个 URL（网页来源）或相对路径（知识库文件来源）"
        ),
    )


def _is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source))


def _is_local_path(source: str) -> bool:
    return bool(re.match(r"^(knowledge|memory|skills|workspace)/", source))


def _run_async(coro):
    """安全执行异步协程，兼容事件循环已存在和未存在的场景。"""
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


class GetFullInformationTool(BaseTool):
    name: str = "get_full_information"
    description: str = (
        "获取回答来源的完整原始文本。当用户质疑上一轮回答中某个来源的信息准确性时，"
        "传入该来源的 URL 或知识库文件路径，工具会返回对应文档的完整内容（含来源标注），"
        "以便逐字比对原文与回答之间的差异。输入是一个来源字符串列表。"
    )
    args_schema: Type[BaseModel] = GetFullInformationInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _root_dir: Path = PrivateAttr()
    _session: requests.Session = PrivateAttr()
    _worker_pool: WorkerPool = PrivateAttr()
    _scraper: Scraper = PrivateAttr()

    def __init__(self, base_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root_dir = base_dir.resolve()
        settings = get_settings()
        self._worker_pool = WorkerPool(
            max_workers=settings.max_concurrency_num,
            rate_limit_delay=settings.rate_limit_delay,
            request_delay=settings.request_delay,
        )
        user_agent = get_user_agent()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        browser_headers = build_browser_headers()
        self._session.headers.update(
            {k: v for k, v in browser_headers.items() if k != "User-Agent"}
        )
        self._proxy_provider = None
        if settings.enable_proxy:
            from proxy.free_proxy_provider import FreeProxyProvider
            self._proxy_provider = FreeProxyProvider(pool_size=settings.proxy_pool_size)
        # 仅用于调用 get_scraper 方法，不实际批量抓取
        self._scraper = Scraper([], user_agent, "bs", self._worker_pool, proxy_provider=self._proxy_provider)

    # ---- 本地文件（仿照 compression.py：仅 pdf / txt）----

    def _resolve_local(self, source: str) -> Path | None:
        candidate = (self._root_dir / source).resolve()
        if self._root_dir not in candidate.parents and candidate != self._root_dir:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def _read_local(self, source: str) -> str:
        file_path = self._resolve_local(source)
        if file_path is None:
            return f"[错误] 本地文件不存在或路径越界: {source}"

        ext = file_path.suffix.lower()
        if ext not in (".pdf", ".txt"):
            return f"[错误] 不支持的文件类型 ({ext})，仅支持 .pdf 和 .txt: {source}"

        header = f"[来源] {source}"

        if ext == ".pdf":
            try:
                scraper = PyMuPDFScraper(str(file_path))
                content, title = scraper.scrape()
            except Exception as e:
                return f"{header}\n\n[错误] PDF 解析失败: {e}"
            if not content:
                return f"{header}\n\n[错误] PDF 内容为空"
            if title:
                header += f" | 标题: {title}"
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_bytes().decode("utf-8", errors="replace")
            title = file_path.name
            header += f" | 文件名: {title}"

        truncated = content[:20000]
        suffix = "\n…[内容过长已截断]" if len(content) > 20000 else ""
        return f"{header}\n\n{truncated}{suffix}"

    # ---- 异步抓取方法（并发优化）----

    async def _fetch_url_async(self, source: str) -> str:
        """异步抓取单个 URL，带速率限制和指数退避重试。"""
        async with self._worker_pool.throttle():
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    ScraperCls = self._scraper.get_scraper(source)
                    scraper = ScraperCls(source, self._session)

                    if hasattr(scraper, "scrape_async"):
                        content, title = await scraper.scrape_async()
                    else:
                        content, title = await asyncio.get_running_loop().run_in_executor(
                            self._worker_pool.executor, scraper.scrape
                        )
                    break  # 成功，跳出重试循环
                except Exception as e:
                    if attempt == max_retries - 1:
                        return f"[来源] {source}\n\n[错误] 抓取失败（重试{max_retries}次）: {e}"
                    await asyncio.sleep(2 ** attempt)

        if not content or len(content) < 50:
            return f"[来源] {source}\n\n[错误] 抓取结果为空或内容过短"

        truncated = content[:20000]
        suffix = "\n…[内容过长已截断]" if len(content) > 20000 else ""
        header = f"[来源] {source}"
        if title:
            header += f" | 标题: {title}"
        return f"{header}\n\n{truncated}{suffix}"

    async def _read_local_async(self, source: str) -> str:
        """异步包装 _read_local，避免阻塞事件循环。"""
        return await asyncio.to_thread(self._read_local, source)

    async def _process_source_async(self, source: str) -> str:
        """根据来源类型分发到对应的异步处理方法。"""
        if _is_url(source):
            return await self._fetch_url_async(source)
        elif _is_local_path(source):
            return await self._read_local_async(source)
        else:
            return (
                f"[来源] {source}\n\n[错误] 无法识别的来源格式，"
                "请提供 http(s):// URL 或 knowledge/xxx 路径"
            )

    # ---- 主逻辑 ----

    def _run(
        self,
        sources: list[str],
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        result = _run_async(self._arun(sources, None))
        status = getattr(self, '_status_msg', None)
        if status:
            from tools import current_tool_status
            current_tool_status.set({"status_msg": status})
        return result

    async def _arun(
        self,
        sources: list[str],
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        tasks = [self._process_source_async(src) for src in sources]
        parts = await asyncio.gather(*tasks, return_exceptions=True)
        _short_names: list[str] = []
        for _s in sources:
            if _s.startswith("http"):
                try:
                    from urllib.parse import urlparse as _urlparse
                    _h = _urlparse(_s).hostname or _s
                    _short_names.append(_h[:35])
                except Exception:
                    _short_names.append(_s[:35])
            else:
                _short_names.append(Path(_s).name[:35])
        self._status_msg = f"获得了 {', '.join(_short_names)} 的全部信息"
        result_parts: list[str] = []
        for i, part in enumerate(parts):
            if isinstance(part, Exception):
                result_parts.append(f"[来源] {sources[i]}\n\n[错误] {part}")
            else:
                result_parts.append(part)
        return "\n\n---\n\n".join(result_parts)
