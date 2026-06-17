"""Web scraper module for GPT Researcher.

This module provides the Scraper class that extracts content from URLs
using various scraping backends (BeautifulSoup, PyMuPDF, Browser, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests

from tools.crawler_util import build_browser_headers, build_request_context, get_user_agent
from utils.workers import WorkerPool

if TYPE_CHECKING:
    from proxy.base_proxy import ProxyProvider

from . import (
    ArxivScraper,
    BeautifulSoupScraper,
    CDPScraper,
    NoDriverScraper,
    PyMuPDFScraper,
)


def _format_proxy_for_requests(ip_info) -> str | None:
    """将 IpInfoModel 转为 http://user:pass@host:port 格式。"""
    if ip_info is None:
        return None
    if ip_info.user and ip_info.password:
        return f"http://{ip_info.user}:{ip_info.password}@{ip_info.ip}:{ip_info.port}"
    return f"http://{ip_info.ip}:{ip_info.port}"


def _format_proxy_for_nodriver(ip_info) -> str | None:
    """将 IpInfoModel 转为 host:port 格式（不包含协议）。"""
    if ip_info is None:
        return None
    return f"{ip_info.ip}:{ip_info.port}"


class Scraper:
    """
    从网址提取内容的爬虫类
    """

    MAX_RETRIES = 3

    def __init__(self, urls, user_agent, scraper, worker_pool: WorkerPool, url_timeout: float = 60,
                 proxy_provider: ProxyProvider | None = None, cookie_manager=None):
        """
        初始化 Scraper 类.
        Args:
            urls: 要抓取的 URL 列表
            user_agent: (deprecated, kept for callers) UA now rotates per-URL
            url_timeout: 单个 URL 最大抓取秒数
            proxy_provider: 可选的代理提供者，启用时每 URL 用完即弃
            cookie_manager: 可选的 Cookie 持久化管理器
        """
        _ = user_agent  # kept for backward compat, UA rotates per-URL now
        # 重复的 URL 将被移除
        unique_urls = list(dict.fromkeys(urls))  # 保持秩序的同时去除重复项
        duplicates_removed = len(urls) - len(unique_urls)

        self.urls = unique_urls
        self._base_headers = {k: v for k, v in build_browser_headers().items()
                              if k not in ("User-Agent", "Referer")}
        self._sessions: dict[str, requests.Session] = {}
        self._sessions_lock = asyncio.Lock()
        # Per-domain referer tracking — different domains never interfere.
        self._last_url_by_domain: dict[str, str] = {}
        self.scraper = scraper
        self.url_timeout = url_timeout
        self.logger = logging.getLogger(__name__)
        self.worker_pool = worker_pool
        self.proxy_provider = proxy_provider
        self.cookie_manager = cookie_manager

        # 如果发现重复项，则会显示去重结果
        if duplicates_removed > 0:
            self.logger.info(
                f"Removed {duplicates_removed} duplicate URL(s). "
                f"Scraping {len(unique_urls)} unique URLs instead of {len(urls)}."
            )

    async def _get_session_for_url(self, url: str) -> requests.Session:
        """按域名返回隔离的 Session，避免跨站点 Cookie 污染。上限 50 个。"""
        domain = urlparse(url).netloc
        async with self._sessions_lock:
            if domain not in self._sessions:
                if len(self._sessions) >= 50:
                    self._sessions.pop(next(iter(self._sessions)))
                session = requests.Session()
                session.headers.update(self._base_headers)
                if self.cookie_manager:
                    for c in self.cookie_manager.load_cookies(domain):
                        session.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
                self._sessions[domain] = session
            return self._sessions[domain]

    async def save_all_cookies(self) -> None:
        """将所有域名的 Cookies 持久化到磁盘。"""
        if not self.cookie_manager:
            return
        async with self._sessions_lock:
            items = list(self._sessions.items())
        for domain, session in items:
            cookies = [{"name": c.name, "value": c.value, "domain": c.domain,
                        "path": c.path, "expires": c.expires}
                       for c in session.cookies if c.name]
            if cookies:
                self.cookie_manager.save_cookies(domain, cookies)

    async def run(self):
        """
        从网址提取内容的主函数，使用异步方式并行处理多个 URL。
        每个 URL 有独立超时，单个失败不影响其余任务。
        返回所有成功抓取的结果列表。
        """
        results = []
        async for item in self.run_streaming():
            if item.get("raw_content"):
                results.append(item)
        return results

    async def run_streaming(self):
        """
        流式版本：每完成一个 URL 的抓取就立即 yield 结果，不等待其他 URL。
        调用方可以边抓取边处理（存缓存、建索引），实现真正的并发流水线。
        """
        max_workers = self.worker_pool.max_workers
        overall_timeout = max(len(self.urls) / max(max_workers, 1), 1) * self.url_timeout * 1.5

        async def _fetch_with_timeout(url):
            try:
                return await asyncio.wait_for(
                    self.extract_data_from_url(url),
                    timeout=self.url_timeout,
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout ({self.url_timeout}s) fetching {url}")
                return {"url": url, "raw_content": None, "title": ""}
            except Exception as e:
                self.logger.error(f"Error fetching {url}: {e}")
                return {"url": url, "raw_content": None, "title": ""}

        tasks = [asyncio.create_task(_fetch_with_timeout(url)) for url in self.urls]
        try:
            for coro in asyncio.as_completed(tasks, timeout=overall_timeout):
                try:
                    result = await coro
                except Exception:
                    continue
                if isinstance(result, dict):
                    if result.get("raw_content"):
                        self.logger.info(f"[streaming] Scraped: {result.get('url', '?')} ({len(result.get('raw_content', ''))} chars)")
                    yield result
        except asyncio.TimeoutError:
            self.logger.warning(f"Overall scrape timeout ({overall_timeout:.0f}s) exceeded")
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _scrape_with_retry(self, scraper):
        """指数退避重试抓取，最多 MAX_RETRIES 次。"""
        for attempt in range(self.MAX_RETRIES):
            try:
                if hasattr(scraper, "scrape_async"):
                    return await scraper.scrape_async()
                else:
                    return await asyncio.get_running_loop().run_in_executor(
                        self.worker_pool.executor, scraper.scrape
                    )
            except Exception:
                if attempt == self.MAX_RETRIES - 1:
                    raise
                delay = (2 ** attempt) * random.uniform(0.5, 1.5)  # jittered 0.5-1.5s, 1-3s, 2-6s
                self.logger.warning(
                    f"Scrape attempt {attempt + 1}/{self.MAX_RETRIES} failed, "
                    f"retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

    async def extract_data_from_url(self, link, session=None):
        """
        从链接提取内容并记录日志。两级降级策略：轻量抓取器失败时自动切换 NoDriver 兜底。
        """
        async with self.worker_pool.throttle():
            try:
                domain_session = await self._get_session_for_url(link)
                current_domain = urlparse(link).netloc

                # Per-domain Referer with lock-protected read.
                async with self._sessions_lock:
                    last_url = self._last_url_by_domain.get(current_domain)
                if last_url:
                    domain_session.headers["Referer"] = last_url
                else:
                    domain_session.headers.pop("Referer", None)

                is_web_page = not link.endswith(".pdf") and "arxiv.org" not in link
                primary_cls = self.get_scraper(link)
                primary_name = primary_cls.__name__

                # 要依次尝试的抓取器：(类, 名称, 是否重试)
                candidates: list[tuple[type, str]] = [(primary_cls, primary_name)]
                if is_web_page and primary_name != "NoDriverScraper":
                    candidates.append((NoDriverScraper, "NoDriverScraper"))

                content, title = "", ""
                tried: list[str] = []

                # Fetch a fresh proxy for this URL (use-and-discard)
                proxy_info = None
                if self.proxy_provider:
                    try:
                        proxies = await self.proxy_provider.get_proxy(1)
                        if proxies:
                            proxy_info = proxies[0]
                    except Exception:
                        self.logger.debug("Failed to fetch proxy, continuing without")

                for scraper_cls, name in candidates:
                    tried.append(name)
                    current_ua = get_user_agent()
                    scraper_kwargs = {"user_agent": current_ua}
                    if proxy_info:
                        if scraper_cls in (NoDriverScraper, CDPScraper):
                            scraper_kwargs["proxy_server"] = _format_proxy_for_nodriver(proxy_info)
                        else:
                            scraper_kwargs["proxy_url"] = _format_proxy_for_requests(proxy_info)
                    scraper = scraper_cls(link, domain_session, **scraper_kwargs)
                    self.logger.info(f"\n=== Using {name} (attempt {len(tried)}/{len(candidates)}) ===")

                    try:
                        content, title = await self._scrape_with_retry(scraper)
                    except Exception as e:
                        self.logger.warning(f"{name} failed for {link}: {e}")
                        continue

                    if len(content) >= 100:
                        break
                    if name != "NoDriverScraper":
                        self.logger.info(
                            f"Content too short ({len(content)} chars), "
                            f"falling back to NoDriver..."
                        )

                if len(content) < 100:
                    self.logger.warning(
                        f"Content too short or empty for {link} "
                        f"(tried: {', '.join(tried)})"
                    )
                    return {"url": link, "raw_content": None, "title": title}

                self.logger.info(f"\nTitle: {title}")
                self.logger.info(f"Content length: {len(content)} characters")
                self.logger.info(f"URL: {link}")
                self.logger.info("=" * 50)

                async with self._sessions_lock:
                    self._last_url_by_domain[current_domain] = link
                return {"url": link, "raw_content": content, "title": title}

            except Exception as e:
                self.logger.error(f"Error processing {link}: {str(e)}")
                return {"url": link, "raw_content": None, "title": ""}

    def get_scraper(self, link):
        """
        函数 `get_scraper` 会根据所提供的链接或在无匹配项的情况下使用默认的爬虫类来确定合适的爬虫类。
        
        参数：
        link: `get_scraper` 方法接受一个名为“link”的参数，该参数是一个指向网页或 PDF 文件的 URL 链接。根据该链接所
        指向的内容的类型，该方法会确定使用适当的爬虫类来从该内容中提取数据。
        
        return:
        “get_scraper” 方法会根据所提供的链接返回相应的爬虫类。该方法会检查链接，依据在“SCRAPER_CLASSES”字典中预先设
        定的映射关系来确定要使用的合适爬虫类。如果链接以“.pdf”结尾，就会选择“PyMuPDFScraper”类。如果链接包含
        “arxiv.org”，则会选择“ArxivScraper”类。
        """

        SCRAPER_CLASSES = {
            "pdf": PyMuPDFScraper,
            "arxiv": ArxivScraper,
            "bs": BeautifulSoupScraper,
            "nodriver": NoDriverScraper,
            "cdp": CDPScraper,
        }

        scraper_key = None

        if link.endswith(".pdf"):
            scraper_key = "pdf"
        elif "arxiv.org" in link:
            scraper_key = "arxiv"
        else:
            scraper_key = self.scraper

        scraper_class = SCRAPER_CLASSES.get(scraper_key)
        if scraper_class is None:
            raise Exception("Scraper not found.")

        return scraper_class
