from contextlib import asynccontextmanager
import math
from pathlib import Path
import random
import traceback
from urllib.parse import urlparse
from typing import Dict, Literal, cast, Tuple, List
import requests
import asyncio
import logging

from ..utils import parse_html
from tools.crawler_util import get_user_agent, get_stealth_js


class NoDriverScraper:
    logger = logging.getLogger(__name__)
    max_browsers = 5
    browser_load_threshold = 8
    browsers: set["NoDriverScraper.Browser"] = set()
    browsers_lock = asyncio.Lock()

    @staticmethod
    def get_domain(url: str) -> str:
        domain = urlparse(url).netloc
        parts = domain.split(".")
        if len(parts) > 2:
            domain = ".".join(parts[-2:])
        return domain

    class Browser:
        def __init__(
            self,
            driver: "zendriver.Browser",
        ):
            self.driver = driver
            self.processing_count = 0
            self.has_blank_page = True
            self.allowed_requests_times = {}
            self.domain_semaphores: Dict[str, asyncio.Semaphore] = {}
            self.tab_mode = True
            self.max_scroll_percent = 500
            self.stopping = False

        async def get(self, url: str) -> "zendriver.Tab":
            async with self.rate_limit_for_domain(url):
                new_window = not self.has_blank_page
                self.has_blank_page = False
                if self.tab_mode:
                    tab = await self.driver.get(url, new_tab=new_window)
                else:
                    tab = await self.driver.get(url, new_window=new_window)
                if tab is None:
                    return None
                # Inject anti-fingerprinting script before page interaction
                try:
                    stealth_js = get_stealth_js()
                    await tab.evaluate(stealth_js)
                except Exception:
                    pass
            return tab

        async def scroll_page_to_bottom(self, page: "zendriver.Tab"):
            total_scroll_percent = 0
            while True:
                # in tab mode, we need to bring the tab to front before scrolling to load the page content properly
                if self.tab_mode:
                    await page.bring_to_front()
                scroll_percent = random.randrange(46, 97)
                total_scroll_percent += scroll_percent
                await page.scroll_down(scroll_percent)
                await self.wait_or_timeout(page, "idle", 2)
                await page.sleep(random.uniform(0.23, 0.56))

                if total_scroll_percent >= self.max_scroll_percent:
                    break

                if cast(
                    bool,
                    await page.evaluate(
                        "window.innerHeight + window.scrollY >= document.scrollingElement.scrollHeight"
                    ),
                ):
                    break

        async def wait_or_timeout(
            self,
            page: "zendriver.Tab",
            until: Literal["complete", "idle"] = "idle",
            timeout: float = 3,
        ):
            try:
                if until == "idle":
                    await asyncio.wait_for(page.wait(), timeout)
                else:
                    timeout = math.ceil(timeout)
                    await page.wait_for_ready_state(until, timeout=timeout)
            except asyncio.TimeoutError:
                NoDriverScraper.logger.debug(
                    f"timeout waiting for {until} after {timeout} seconds"
                )

        async def close_page(self, page: "zendriver.Tab"):
            try:
                await page.close()
            except Exception as e:
                NoDriverScraper.logger.error(f"Failed to close page: {e}")
            finally:
                self.processing_count -= 1

        @asynccontextmanager
        async def rate_limit_for_domain(self, url: str):
            semaphore = None
            try:
                domain = NoDriverScraper.get_domain(url)

                semaphore = self.domain_semaphores.get(domain)
                if not semaphore:
                    semaphore = asyncio.Semaphore(1)
                    self.domain_semaphores[domain] = semaphore

                was_locked = semaphore.locked()
                async with semaphore:
                    if was_locked:
                        await asyncio.sleep(random.uniform(0.6, 1.2))
                    yield

            except Exception as e:
                # Log error but don't block the request
                NoDriverScraper.logger.warning(
                    f"Rate limiting error for {url}: {str(e)}"
                )

        async def stop(self):
            if self.stopping:
                return
            self.stopping = True
            await self.driver.stop()

    @classmethod
    async def get_browser(cls, headless: bool = True, proxy_server: str | None = None) -> "NoDriverScraper.Browser":
        async def create_browser():
            try:
                global zendriver
                import zendriver
            except ImportError:
                raise ImportError(
                    "The zendriver package is required to use NoDriverScraper. "
                    "Please install it with: pip install zendriver"
                )

            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--exclude-switches=enable-automation",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--disable-prompt-on-repost",
                "--disable-sync",
            ]
            if proxy_server:
                browser_args.append(f"--proxy-server={proxy_server}")

            config = zendriver.Config(
                headless=headless,
                browser_connection_timeout=30,
                user_agent=get_user_agent(),
                browser_args=browser_args,
            )
            driver = await zendriver.start(config)
            browser = cls.Browser(driver)
            cls.browsers.add(browser)
            return browser

        # Decide under lock whether to create a new browser or pick
        # an existing one.  Actual creation (slow I/O) happens outside
        # the lock so peers are not blocked during Chromium startup.
        need_new = False
        chosen_browser: "NoDriverScraper.Browser" | None = None

        async with cls.browsers_lock:
            if len(cls.browsers) == 0:
                need_new = True
            else:
                # Pick randomly among browsers below the load threshold
                # to spread fingerprint across different instances.
                low_load = [b for b in cls.browsers
                            if b.processing_count < cls.browser_load_threshold]
                if low_load:
                    chosen_browser = random.choice(low_load)
                elif len(cls.browsers) < cls.max_browsers:
                    need_new = True
                else:
                    # At max capacity — fall back to least-loaded.
                    chosen_browser = min(cls.browsers,
                                         key=lambda b: b.processing_count)
                if chosen_browser is not None:
                    chosen_browser.processing_count += 1

        if need_new:
            browser = await create_browser()
            browser.processing_count += 1
            return browser
        return chosen_browser

    @classmethod
    async def release_browser(cls, browser: Browser):
        async with cls.browsers_lock:
            if browser and browser.processing_count <= 0:
                try:
                    await browser.stop()
                except Exception as e:
                    NoDriverScraper.logger.error(f"Failed to release browser: {e}")
                finally:
                    cls.browsers.discard(browser)

    def __init__(self, url: str, session: requests.Session | None = None, proxy_server: str | None = None, **_kwargs):
        self.url = url
        self.session = session
        self.proxy_server = proxy_server
        self.debug = False

    async def scrape_async(self) -> Tuple[str, str]:
        """Returns tuple of (text, title)"""
        if not self.url:
            return (
                "A URL was not specified, cancelling request to browse website.",
                "",
            )

        browser: NoDriverScraper.Browser | None = None
        page = None
        try:
            try:
                browser = await self.get_browser(proxy_server=self.proxy_server)
            except ImportError as e:
                self.logger.error(f"Failed to initialize browser: {str(e)}")
                return str(e), ""

            page = await browser.get(self.url)
            if page is None:
                return "Browser failed to open page (returned None)", ""
            await browser.wait_or_timeout(page, "complete", 2)
            # wait for potential redirection
            await page.sleep(random.uniform(0.3, 0.7))
            await browser.wait_or_timeout(page, "idle", 2)

            await browser.scroll_page_to_bottom(page)
            html = await page.get_content()
            text, title = parse_html(html)

            if len(text) < 200:
                self.logger.warning(
                    f"Content is too short from {self.url}. Title: {title}, Text length: {len(text)},\n"
                    f"excerpt: {text}."
                )
                if self.debug:
                    screenshot_dir = Path("logs/screenshots")
                    screenshot_dir.mkdir(exist_ok=True)
                    screenshot_path = (
                        screenshot_dir
                        / f"screenshot-error-{NoDriverScraper.get_domain(self.url)}.jpeg"
                    )
                    await page.save_screenshot(screenshot_path)
                    self.logger.warning(
                        f"check screenshot at [{screenshot_path}] for more details."
                    )

            return text, title
        except Exception as e:
            self.logger.error(
                f"An error occurred during scraping: {str(e)}\n"
                "Full stack trace:\n"
                f"{traceback.format_exc()}"
            )
            return str(e), ""
        finally:
            try:
                if page and browser:
                    await browser.close_page(page)
                if browser:
                    await self.release_browser(browser)
            except Exception as e:
                self.logger.error(e)
