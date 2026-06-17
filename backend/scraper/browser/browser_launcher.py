"""自动检测和启动 Chrome/Edge 浏览器的 CDP 启动器。

从 MediaCrawler 移植，适配 myagent 的 logging 风格。
支持 Windows / macOS / Linux 三平台。
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import signal
import socket
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


class BrowserLauncher:
    """自动检测系统上的 Chrome/Edge/Chromium，以 CDP 调试模式启动。"""

    def __init__(self) -> None:
        self.system = _platform.system()
        self.browser_process: subprocess.Popen | None = None
        self.debug_port: int | None = None

    # ── 浏览器路径检测 ───────────────────────────────────────────

    def detect_browser_paths(self) -> list[str]:
        """按优先级返回可用的浏览器可执行文件路径列表。"""
        if self.system == "Windows":
            possible = [
                os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome Beta\Application\chrome.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome Dev\Application\chrome.exe"),
            ]
        elif self.system == "Darwin":
            possible = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
                "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
            ]
        else:
            possible = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/google-chrome-beta",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/snap/bin/chromium",
                "/usr/bin/microsoft-edge",
                "/usr/bin/microsoft-edge-stable",
            ]

        found: list[str] = []
        for path in possible:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                found.append(path)
        return found

    # ── 端口管理 ─────────────────────────────────────────────────

    def find_available_port(self, start_port: int = 9222) -> int:
        """从 start_port 开始扫描，返回第一个可用的 TCP 端口。"""
        port = start_port
        while port < start_port + 100:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    port += 1
        raise RuntimeError(f"无法在 {start_port}-{port - 1} 范围内找到可用端口")

    # ── 启动浏览器 ───────────────────────────────────────────────

    def launch_browser(
        self,
        browser_path: str,
        debug_port: int,
        headless: bool = False,
        user_data_dir: str | None = None,
    ) -> subprocess.Popen:
        """以 CDP 调试模式启动浏览器，返回子进程对象。"""
        args = [
            browser_path,
            f"--remote-debugging-port={debug_port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            # 反检测参数
            "--disable-blink-features=AutomationControlled",
            "--exclude-switches=enable-automation",
            "--disable-infobars",
        ]

        if headless:
            args.extend(["--headless=new", "--disable-gpu"])
        else:
            args.append("--start-maximized")

        if user_data_dir and user_data_dir.strip():
            os.makedirs(user_data_dir, exist_ok=True)
            args.append(f"--user-data-dir={user_data_dir}")

        logger.info("[BrowserLauncher] 启动浏览器: %s", browser_path)
        logger.info("[BrowserLauncher] 调试端口: %s, 无头: %s", debug_port, headless)

        try:
            if self.system == "Windows":
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
            self.browser_process = process
            self.debug_port = debug_port
            return process
        except Exception as e:
            logger.error("[BrowserLauncher] 启动浏览器失败: %s", e)
            raise

    # ── 等待就绪 ─────────────────────────────────────────────────

    def wait_for_browser_ready(self, debug_port: int, timeout: int = 30) -> bool:
        """轮询等待 CDP 端口可用。"""
        logger.info("[BrowserLauncher] 等待浏览器在端口 %s 就绪（最多 %ds）...", debug_port, timeout)
        start = time.time()
        while time.time() - start < timeout:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                if s.connect_ex(("127.0.0.1", debug_port)) == 0:
                    logger.info("[BrowserLauncher] 浏览器就绪 (端口 %s)", debug_port)
                    return True
            time.sleep(0.5)
        logger.error("[BrowserLauncher] 浏览器在 %ds 内未能就绪", timeout)
        return False

    # ── 浏览器信息 ───────────────────────────────────────────────

    def get_browser_info(self, browser_path: str) -> tuple[str, str]:
        """返回 (浏览器名称, 版本号)。"""
        name = "Unknown Browser"
        lower = browser_path.lower()
        if "edge" in lower or "msedge" in lower:
            name = "Microsoft Edge"
        elif "chrome" in lower:
            name = "Google Chrome"
        elif "chromium" in lower:
            name = "Chromium"

        version = "Unknown Version"
        try:
            result = subprocess.run(
                [browser_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout:
                version = result.stdout.strip()
        except Exception:
            pass

        return name, version

    # ── 清理 ─────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """关闭浏览器进程。"""
        process = self.browser_process
        if not process or process.poll() is not None:
            logger.debug("[BrowserLauncher] 浏览器进程已退出，无需清理")
            self.browser_process = None
            return

        logger.info("[BrowserLauncher] 正在关闭浏览器进程...")
        try:
            if self.system == "Windows":
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("[BrowserLauncher] 正常终止超时，强制关闭")
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        capture_output=True,
                        check=False,
                    )
            else:
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
                    process.wait(timeout=5)
            logger.info("[BrowserLauncher] 浏览器进程已关闭")
        except Exception as e:
            logger.warning("[BrowserLauncher] 关闭浏览器时出错: %s", e)
        finally:
            self.browser_process = None
