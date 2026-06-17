from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    backend_dir: Path
    project_root: Path

    retrievers: list[str] = field(default_factory=lambda: ["tavily"])
    retrieval_domains: list[str] = field(default_factory=list)

    fast_llm_model: str = "deepseek-v4-flash"
    fast_llm_api_key: str | None = None
    fast_llm_base_url: str | None = None

    smart_llm_model: str = "deepseek-v4-pro"
    smart_llm_api_key: str | None = None
    smart_llm_base_url: str | None = None


    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None

    reranker_model: str = "BAAI/bge-reranker-v2-m3"
  
    rag_mode: bool = False
    component_char_limit: int = 20_000
    terminal_timeout_seconds: int = 30
    max_iterations: int = 3
    max_retrieved_urls: int = 5

    # 防爬配置
    request_delay: float = 0.5
    max_concurrency_num: int = 3
    rate_limit_delay: float = 0.5
    request_timeout: float = 20.0
    enable_proxy: bool = False
    proxy_pool_size: int = 5
    cookie_persistence: bool = False

    # CDP (Chrome DevTools Protocol) 浏览器配置
    cdp_headless: bool = True              # True=无头, False=可见窗口（反检测更强）
    cdp_connect_existing: bool = False     # 连接用户已打开的浏览器
    cdp_debug_port: int = 9222             # CDP 调试起始端口
    cdp_custom_browser_path: str = ""      # 自定义浏览器路径（空=自动检测）
    cdp_browser_launch_timeout: int = 60   # 等待浏览器就绪的超时秒数
    cdp_auto_close_browser: bool = True    # 退出时自动关闭浏览器进程
    cdp_user_data_dir: str = ""            # 持久化浏览器数据目录（空=临时）

class RuntimeConfig:
    """运行时可切换的配置（非 frozen，允许动态修改）。"""

    _rag_mode: bool

    def __init__(self) -> None:
        self._rag_mode = bool(os.getenv("RAG_MODE", "").lower() == "true")

    def get_rag_mode(self) -> bool:
        return self._rag_mode

    def set_rag_mode(self, enabled: bool) -> None:
        self._rag_mode = enabled


runtime_config = RuntimeConfig()


# -- helpers -------------------------------------------------------------------

def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


# -- single entry point --------------------------------------------------------

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    backend_dir = Path(__file__).resolve().parent
    load_dotenv(backend_dir / ".env")
    retriever = os.getenv("RETRIEVERS", "tavily")#默认使用tavily检索器

    
    return Settings(
        backend_dir=backend_dir,
        project_root=backend_dir.parent,
        retrievers=[retriever],
        fast_llm_model=os.getenv("FAST_LLM_MODEL", "deepseek-v4-flash"),
        fast_llm_api_key=_first_env("FAST_LLM_API_KEY", "OPENAI_API_KEY"),
        fast_llm_base_url=_first_env("FAST_LLM_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL"),
        smart_llm_model=os.getenv("SMART_LLM_MODEL", "deepseek-v4-pro"),
        smart_llm_api_key=_first_env("SMART_LLM_API_KEY", "OPENAI_API_KEY"),
        smart_llm_base_url=_first_env("SMART_LLM_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL"),
        embedding_model=os.getenv("EMBEDDING_MODEL") or None,
        embedding_api_key=os.getenv("EMBEDDING_API_KEY") or None,
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL") or None,
        reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        rag_mode=os.getenv("RAG_MODE", "false").lower() == "true",
        max_retrieved_urls=int(os.getenv("MAX_RETRIEVED_URLS", "7")),
        request_delay=float(os.getenv("REQUEST_DELAY", "0.5")),
        max_concurrency_num=int(os.getenv("MAX_CONCURRENCY_NUM", "3")),
        rate_limit_delay=float(os.getenv("RATE_LIMIT_DELAY", "0.5")),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "20.0")),
        enable_proxy=os.getenv("ENABLE_PROXY", "false").lower() == "true",
        proxy_pool_size=int(os.getenv("PROXY_POOL_SIZE", "5")),
        cookie_persistence=os.getenv("COOKIE_PERSISTENCE", "false").lower() == "true",
        cdp_headless=os.getenv("CDP_HEADLESS", "true").lower() == "true",
        cdp_connect_existing=os.getenv("CDP_CONNECT_EXISTING", "false").lower() == "true",
        cdp_debug_port=int(os.getenv("CDP_DEBUG_PORT", "9222")),
        cdp_custom_browser_path=os.getenv("CDP_CUSTOM_BROWSER_PATH", ""),
        cdp_browser_launch_timeout=int(os.getenv("CDP_BROWSER_LAUNCH_TIMEOUT", "60")),
        cdp_auto_close_browser=os.getenv("CDP_AUTO_CLOSE_BROWSER", "true").lower() == "true",
        cdp_user_data_dir=os.getenv("CDP_USER_DATA_DIR", ""),
    )
