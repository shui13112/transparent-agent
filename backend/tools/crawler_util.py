"""UA 池 + 浏览器请求头工具。

对标 MediaCrawler 的爬虫工具函数，提供：
- 50+ 个 Chrome/Edge 版本的 UA 池（按平台分组），随机选取
- 平台感知：sec-ch-ua-platform 自动匹配 UA 中的 OS
- sec-ch-ua-arch / sec-ch-ua-bitness 客户端提示头
- 完整的浏览器/API 请求头链
"""

from __future__ import annotations

import platform as _platform
import re
import random
from urllib.parse import urlparse

# ── UA 池（按平台分组，Chrome 120-134 + Edge 变体）─────────────────

_UA_WINDOWS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_UA_MAC = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_UA_LINUX = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_UA_EDGE = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

_FULL_UA_POOL = _UA_WINDOWS + _UA_MAC + _UA_LINUX + _UA_EDGE

_MOBILE_UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
]

_CHROME_VERSION_RE = re.compile(r"Chrome/(\d+)\.")
_EDGE_VERSION_RE = re.compile(r"Edg/(\d+)\.")

# ── 平台检测 ──────────────────────────────────────────────────────


def _detect_ua_platform(user_agent: str) -> str:
    """从 UA 字符串推断浏览器运行的平台，用于对齐 sec-ch-ua-platform。"""
    if "Windows" in user_agent:
        return '"Windows"'
    elif "Macintosh" in user_agent:
        return '"macOS"'
    elif "Linux" in user_agent or "X11" in user_agent:
        return '"Linux"'
    return '"Windows"'


def _detect_ua_arch(user_agent: str) -> str:
    """从 UA 字符串推断 CPU 架构，用于 sec-ch-ua-arch。"""
    if "Macintosh" in user_agent:
        return '"arm"' if "ARM" in user_agent else '"x86"'
    if "x64" in user_agent or "Win64" in user_agent:
        return '"x86"'
    return '"x86"'


def _parse_chrome_version(user_agent: str) -> str:
    m = _CHROME_VERSION_RE.search(user_agent)
    return m.group(1) if m else "134"


# ── sec-ch-ua 构建 ────────────────────────────────────────────────


def build_sec_ch_ua(chrome_version: str, has_edge: bool = False) -> str:
    """构建 sec-ch-ua 头，与传入 UA 的 Chrome 版本号对齐。"""
    base = f'"Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}", "Not.A/Brand";v="99"'
    if has_edge:
        base = f'"Chromium";v="{chrome_version}", "Microsoft Edge";v="{chrome_version}", "Not.A/Brand";v="99"'
    return base


# ── UA 选取 ───────────────────────────────────────────────────────


def get_user_agent() -> str:
    """从完整 UA 池中随机返回一个桌面 UA（保持向后兼容）。"""
    return random.choice(_FULL_UA_POOL)


def get_platform_aware_ua() -> str:
    """返回与当前运行 OS 匹配的 UA（70% 概率匹配，30% 跨平台以分散指纹）。"""
    system = _platform.system()
    if system == "Windows":
        primary = _UA_WINDOWS + _UA_EDGE
    elif system == "Darwin":
        primary = _UA_MAC + _UA_EDGE
    else:
        primary = _UA_LINUX
    if random.random() < 0.3:
        return random.choice(_FULL_UA_POOL)
    return random.choice(primary)


def get_mobile_user_agent() -> str:
    """返回移动端 Safari UA。"""
    return random.choice(_MOBILE_UA_POOL)


# ── 浏览器请求头链 ──────────────────────────────────────────────


def build_browser_headers(host: str = "", user_agent: str | None = None) -> dict:
    """构建完整浏览器导航请求头，模拟 Chrome 首次访问一个站点。

    sec-ch-ua 系列与 User-Agent 自动对齐（平台 + 版本号）。
    sec-fetch-* 系列设为首次导航（``sec-fetch-site: none``、``sec-fetch-user: ?1``）。
    """
    ua = user_agent or get_platform_aware_ua()
    chrome_ver = _parse_chrome_version(ua)
    has_edge = "Edg/" in ua
    platform = _detect_ua_platform(ua)
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "sec-ch-ua": build_sec_ch_ua(chrome_ver, has_edge),
        "sec-ch-ua-arch": _detect_ua_arch(ua),
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": ua,
    }


def build_api_headers(host: str, referer: str = "", user_agent: str | None = None) -> dict:
    """构建 XHR/fetch 请求头，模拟页面内 JS 发起的 API 调用。

    sec-ch-ua 系列与 User-Agent 自动对齐。
    sec-fetch-* 系列设为同站 CORS 请求（``sec-fetch-site: same-origin``）。
    """
    ua = user_agent or get_platform_aware_ua()
    chrome_ver = _parse_chrome_version(ua)
    has_edge = "Edg/" in ua
    platform = _detect_ua_platform(ua)
    origin = f"https://{host}" if host else ""
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": origin,
        "Pragma": "no-cache",
        "Referer": referer or f"{origin}/",
        "sec-ch-ua": build_sec_ch_ua(chrome_ver, has_edge),
        "sec-ch-ua-arch": _detect_ua_arch(ua),
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "User-Agent": ua,
    }


def build_request_context(url: str = "", referer: str = "") -> dict:
    """一站式返回一致的请求上下文（UA + 完整浏览器头 + Referer）。

    供 Scraper 使用，确保单次请求的 UA / sec-ch-ua / platform 三方对齐。
    """
    ua = get_platform_aware_ua()
    host = urlparse(url).hostname if url else ""
    headers = build_browser_headers(host=host, user_agent=ua)
    if referer:
        headers["Referer"] = referer
    return {"user_agent": ua, "headers": headers}


# ── 读取 stealth.min.js ───────────────────────────────────────────

_STEALTH_JS: str | None = None


def get_stealth_js() -> str:
    """加载 libs/stealth.min.js 内容（缓存读取）。"""
    global _STEALTH_JS
    if _STEALTH_JS is None:
        from pathlib import Path

        js_path = Path(__file__).resolve().parent.parent / "libs" / "stealth.min.js"
        _STEALTH_JS = js_path.read_text(encoding="utf-8")
    return _STEALTH_JS
