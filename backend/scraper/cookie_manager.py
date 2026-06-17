import json
import logging
import time
from pathlib import Path
from http.cookiejar import Cookie, MozillaCookieJar

logger = logging.getLogger(__name__)

_COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days


class CookieManager:
    """按域名持久化 Cookie，跨运行复用登录态。"""

    def __init__(self, cookies_dir: str = "cookies") -> None:
        self._dir = Path(cookies_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._jar = MozillaCookieJar()

    def _cookie_file(self, domain: str) -> Path:
        safe = domain.replace(":", "_").replace("/", "_")
        return self._dir / f"{safe}.json"

    def load_cookies(self, domain: str) -> list[dict]:
        path = self._cookie_file(domain)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cookies = data.get("cookies", [])
            # Filter expired
            now = time.time()
            return [c for c in cookies if c.get("expires", now + 1) > now]
        except Exception:
            logger.debug("Failed to load cookies for %s", domain)
            return []

    def save_cookies(self, domain: str, cookies: list[dict]) -> None:
        path = self._cookie_file(domain)
        try:
            path.write_text(json.dumps({"cookies": cookies, "updated": time.time()}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.debug("Failed to save cookies for %s", domain)
