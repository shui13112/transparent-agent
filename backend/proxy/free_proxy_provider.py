import asyncio
import logging
import random

import httpx

from .base_proxy import ProxyProvider
from .types import IpInfoModel

_FREE_PROXY_APIS = [
    "https://proxylist.geonode.com/api/proxy-list?limit=20&page=1&sort_by=lastChecked&sort_type=desc&protocols=http,https",
]

_PROXY_TEST_URL = "https://httpbin.org/ip"
_PROXY_VALIDATION_TIMEOUT = 5.0
_VALIDATION_CONCURRENCY = 5

logger = logging.getLogger(__name__)


class FreeProxyProvider(ProxyProvider):
    """从公开 API 获取免费代理，验证可用性后入池，用完即弃。"""

    def __init__(self, pool_size: int = 5) -> None:
        self._pool_size = pool_size
        self._pool: list[IpInfoModel] = []

    async def _validate_proxy(self, proxy: IpInfoModel) -> bool:
        """用快速 HTTP 请求验证代理是否可用。"""
        proxy_url = f"http://{proxy.ip}:{proxy.port}"
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, timeout=_PROXY_VALIDATION_TIMEOUT
            ) as client:
                resp = await client.get(_PROXY_TEST_URL)
                return resp.status_code == 200
        except Exception:
            return False

    async def _fetch_proxies(self) -> list[IpInfoModel]:
        sem = asyncio.Semaphore(_VALIDATION_CONCURRENCY)

        async def _validate_one(proxy: IpInfoModel) -> IpInfoModel | None:
            async with sem:
                if await self._validate_proxy(proxy):
                    return proxy
                return None

        for api_url in _FREE_PROXY_APIS:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(api_url)
                    data = resp.json()
                candidates = [
                    IpInfoModel(
                        ip=item["ip"],
                        port=int(item["port"]),
                        protocol=item.get("protocol", "http"),
                    )
                    for item in data.get("data", [])
                ]
                if not candidates:
                    continue

                # Validate concurrently with bounded concurrency
                results = await asyncio.gather(
                    *[_validate_one(p) for p in candidates]
                )
                valid = [r for r in results if r is not None]
                logger.debug(
                    "Free proxy validation: %d/%d working from %s",
                    len(valid), len(candidates), api_url,
                )
                if valid:
                    return valid
            except Exception:
                logger.debug("Free proxy API %s failed, trying next", api_url)
                continue
        return []

    async def get_proxy(self, num: int = 1) -> list[IpInfoModel]:
        if len(self._pool) < num:
            new_proxies = await self._fetch_proxies()
            if new_proxies:
                self._pool = new_proxies
        if not self._pool:
            logger.warning("No working proxies available")
            return []
        proxies = random.sample(self._pool, min(num, len(self._pool)))
        for p in proxies:
            self._pool.remove(p)
        return proxies
