"""通过 Chrome DevTools Protocol 控制真实 Chrome/Edge 浏览器的抓取器。

两种模式（由 config 控制）：
  1. Launch 模式 — 自动检测并启动浏览器
  2. Connect-existing 模式 — 连接用户已打开的浏览器（反检测最强）

依赖 Playwright 的 connect_over_cdp 接口。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import random
import signal
from pathlib import Path
from typing import Tuple

import httpx

from config import get_settings
from tools.crawler_util import build_sec_ch_ua, get_user_agent, _parse_chrome_version

from ..utils import parse_html
from .browser_launcher import BrowserLauncher

logger = logging.getLogger(__name__)


class CDPScraper:
    """CDP 浏览器抓取器（类级单例浏览器 + 上下文复用）。"""

    _playwright = None
    _browser = None
    _context = None
    _launcher: BrowserLauncher | None = None
    _lock = asyncio.Lock()
    _debug_port: int | None = None
    _initialized: bool = False
    _cleanup_registered: bool = False

    def __init__(self, url: str, session=None, proxy_server: str | None = None, **_kwargs) -> None:
        self.url = url
        self.proxy_server = proxy_server
        self._kwargs = _kwargs

    # ── 清理注册 ──────────────────────────────────────────────────

    @classmethod
    def _register_cleanup_handlers(cls) -> None:
        """注册 atexit + 信号处理，确保退出时清理浏览器进程。"""
        if cls._cleanup_registered:
            return

        def _sync_cleanup():
            if cls._launcher and cls._launcher.browser_process:
                logger.info("[CDPScraper] atexit: 清理浏览器进程")
                cls._launcher.cleanup()

        atexit.register(_sync_cleanup)

        prev_sigint = signal.getsignal(signal.SIGINT)
        prev_sigterm = signal.getsignal(signal.SIGTERM)

        def _signal_handler(signum, frame):
            logger.info("[CDPScraper] 收到信号 %s，清理浏览器...", signum)
            if cls._launcher and cls._launcher.browser_process:
                cls._launcher.cleanup()
            if signum == signal.SIGINT and prev_sigint not in (signal.SIG_DFL, signal.default_int_handler):
                return prev_sigint(signum, frame)
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        if prev_sigterm == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, _signal_handler)

        cls._cleanup_registered = True
        logger.info("[CDPScraper] 清理处理器已注册")

    # ── 连接管理 ──────────────────────────────────────────────────

    @classmethod
    async def _ensure_connected(cls, proxy_server: str | None = None) -> None:
        if cls._initialized:
            return
        async with cls._lock:
            if cls._initialized:
                return

            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise ImportError(
                    "CDP 模式需要 playwright。安装: pip install playwright && playwright install"
                )

            settings = get_settings()
            cls._playwright = await async_playwright().start()

            if settings.cdp_connect_existing:
                await cls._connect_existing(settings, proxy_server)
            else:
                await cls._launch_and_connect(settings, proxy_server)

            # 注入反检测脚本
            stealth_js_path = (
                Path(__file__).resolve().parent.parent.parent / "libs" / "stealth.min.js"
            )
            if stealth_js_path.exists():
                await cls._context.add_init_script(path=str(stealth_js_path))
                logger.debug("[CDPScraper] stealth.min.js 已注入")

            cls._initialized = True

    @classmethod
    async def _connect_existing(cls, settings, proxy_server: str | None = None) -> None:
        """连接到用户已打开的浏览器（chrome://inspect/#remote-debugging）。"""
        port = settings.cdp_debug_port
        timeout = settings.cdp_browser_launch_timeout

        logger.info(
            "[CDPScraper] 尝试连接已有浏览器 (端口 %s)...\n"
            "  请确保已在浏览器中启用远程调试: chrome://inspect/#remote-debugging",
            port,
        )

        connected = False
        for i in range(timeout):
            with __import__("socket").socket(__import__("socket").AF_INET, __import__("socket").SOCK_STREAM) as s:
                s.settimeout(1)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    connected = True
                    break
            if i % 10 == 0 and i > 0:
                logger.info("[CDPScraper] 仍在等待浏览器... (%ds)", i)
            await asyncio.sleep(1)

        if not connected:
            raise RuntimeError(
                f"无法连接到端口 {port} 上的浏览器（等待了 {timeout}s）。请确保：\n"
                f"  1. Chrome/Edge 正在运行\n"
                f"  2. 远程调试已启用\n"
                f"  3. 端口设置为 {port}"
            )

        ws_url = await cls._fetch_ws_url(port)
        cls._browser = await cls._playwright.chromium.connect_over_cdp(ws_url)
        cls._debug_port = port

        # 复用已有上下文或创建新的
        contexts = cls._browser.contexts
        if contexts:
            cls._context = contexts[0]
            logger.info("[CDPScraper] 复用已有浏览器上下文")
        else:
            cls._context = await cls._create_context(proxy_server)
        logger.info("[CDPScraper] 已连接到已有浏览器 (端口 %s)", port)

    @classmethod
    async def _launch_and_connect(cls, settings, proxy_server: str | None = None) -> None:
        """自动检测浏览器路径 → 启动 → 等待 CDP → 连接。"""
        cls._launcher = BrowserLauncher()
        cls._register_cleanup_handlers()

        # 1. 确定浏览器路径
        browser_path = settings.cdp_custom_browser_path
        if not browser_path or not Path(browser_path).is_file():
            paths = cls._launcher.detect_browser_paths()
            if not paths:
                raise RuntimeError(
                    "未找到 Chrome/Edge 浏览器。请安装 Chrome 或设置 CDP_CUSTOM_BROWSER_PATH"
                )
            browser_path = paths[0]

        name, version = cls._launcher.get_browser_info(browser_path)
        logger.info("[CDPScraper] 检测到: %s (%s)", name, version)

        # 2. 找可用端口
        cls._debug_port = cls._launcher.find_available_port(settings.cdp_debug_port)

        # 3. 确定用户数据目录
        user_data_dir = settings.cdp_user_data_dir
        if not user_data_dir:
            user_data_dir = str(
                Path(__file__).resolve().parent.parent.parent
                / "browser_data"
                / "cdp_myagent"
            )

        # 4. 启动浏览器
        cls._launcher.launch_browser(
            browser_path=browser_path,
            debug_port=cls._debug_port,
            headless=settings.cdp_headless,
            user_data_dir=user_data_dir,
        )

        # 5. 等待 CDP 就绪
        if not cls._launcher.wait_for_browser_ready(
            cls._debug_port, settings.cdp_browser_launch_timeout
        ):
            raise RuntimeError(
                f"浏览器未能在 {settings.cdp_browser_launch_timeout}s 内就绪"
            )

        # 6. 连接
        ws_url = await cls._fetch_ws_url(cls._debug_port)
        cls._browser = await cls._playwright.chromium.connect_over_cdp(ws_url)

        # 7. 创建上下文
        cls._context = await cls._create_context(proxy_server)
        logger.info("[CDPScraper] 浏览器启动并连接成功 (端口 %s)", cls._debug_port)

    @staticmethod
    async def _fetch_ws_url(port: int) -> str:
        """通过 /json/version 获取 WebSocket URL。"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/json/version", timeout=10)
            data = resp.json()
            ws_url = data.get("webSocketDebuggerUrl")
            if not ws_url:
                raise RuntimeError("未在 /json/version 响应中找到 webSocketDebuggerUrl")
            return ws_url

    @classmethod
    async def _create_context(cls, proxy_server: str | None = None):
        """创建浏览器上下文，UA 使用真实浏览器版本对齐。"""
        # 从真实浏览器版本动态生成匹配的 UA
        browser_version = cls._browser.version if cls._browser else ""
        chrome_ver = browser_version.split(".")[0] if browser_version else "134"
        ua = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{browser_version} Safari/537.36"
            if browser_version
            else get_user_agent()
        )

        context_kwargs: dict = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": ua,
        }
        if proxy_server:
            context_kwargs["proxy"] = {"server": f"http://{proxy_server}"}

        logger.debug("[CDPScraper] 浏览器版本: %s, UA: %s", browser_version, ua)
        return await cls._browser.new_context(**context_kwargs)

    # ── 抓取 ──────────────────────────────────────────────────────

    async def scrape_async(self) -> Tuple[str, str]:
        if not self.url:
            return "未指定 URL。", ""

        try:
            await self._ensure_connected(proxy_server=self.proxy_server)

            page = await self._context.new_page()
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(0.3, 0.7))

                # 轻量验证码检测
                await self._check_and_handle_captcha(page)

                # 懒加载内容滚动
                await page.evaluate("""
                    async () => {
                        await new Promise((resolve) => {
                            let totalHeight = 0;
                            const distance = 300;
                            const maxScrolls = 20;
                            let count = 0;
                            const timer = setInterval(() => {
                                const scrollHeight = document.body.scrollHeight;
                                window.scrollBy(0, distance);
                                totalHeight += distance;
                                count++;
                                if (totalHeight >= scrollHeight || count >= maxScrolls) {
                                    clearInterval(timer);
                                    resolve();
                                }
                            }, 100 + Math.random() * 200);
                        });
                    }
                """)

                html = await page.content()
                text, title = parse_html(html)

                return text, title
            finally:
                await page.close()

        except Exception as e:
            logger.error("[CDPScraper] 抓取错误: %s", e)
            return str(e), ""

    async def _check_and_handle_captcha(self, page) -> None:
        """检测验证码并尝试自动求解滑块验证码。"""
        try:
            from tools.captcha_solver import SliderCaptchaSolver
        except ImportError:
            # opencv-python-headless 未安装时跳过
            return

        try:
            solver = SliderCaptchaSolver()
            detected = await solver.detect_captcha(page)
            if not detected:
                return

            logger.warning("[CDPScraper] 检测到验证码: %s，尝试自动求解...", self.url)

            # 尝试常见滑块验证码选择器
            slider_configs = [
                (".captcha-gap-img", ".captcha-bg-img", ".captcha-slider-btn"),
                (".box-static", ".box-static img", ".box-static"),
                ("img[class*='gap']", "img[class*='bg']", "div[class*='slider']"),
                ("img[class*='piece']", "img[class*='bg']", "div[class*='slider']"),
            ]

            solved = False
            for gap_sel, bg_sel, slider_sel in slider_configs:
                if await solver.solve_slider(page, gap_sel, bg_sel, slider_sel):
                    solved = True
                    break

            if solved:
                logger.info("[CDPScraper] 验证码自动求解成功")
                # 等待页面恢复
                import asyncio
                await asyncio.sleep(2)
            else:
                logger.warning("[CDPScraper] 验证码自动求解失败，返回原始内容")
        except Exception as e:
            logger.debug("[CDPScraper] 验证码处理异常: %s", e)

    # ── 清理 ──────────────────────────────────────────────────────

    @classmethod
    async def cleanup(cls) -> None:
        """清理资源：关闭上下文 → 断开浏览器 → 关闭进程。"""
        settings = get_settings()

        if cls._context:
            try:
                await cls._context.close()
            except Exception:
                pass
            cls._context = None

        if cls._browser:
            try:
                if cls._browser.is_connected():
                    await cls._browser.close()
            except Exception:
                pass
            cls._browser = None

        if cls._playwright:
            try:
                await cls._playwright.stop()
            except Exception:
                pass
            cls._playwright = None

        if cls._launcher and settings.cdp_auto_close_browser:
            cls._launcher.cleanup()
        elif cls._launcher:
            logger.info("[CDPScraper] 浏览器进程保持运行 (CDP_AUTO_CLOSE_BROWSER=False)")

        cls._initialized = False
