"""通用滑块验证码求解器（基于 OpenCV 模板匹配）。

从 MediaCrawler 移植核心算法，适配为通用爬虫使用：
- 接受 CSS 选择器参数，不硬编码任何平台
- 支持从 URL 下载验证码图片
- 人类化拖拽轨迹（缓动函数 + 随机抖动）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

import cv2
import httpx
import numpy as np

from .easing import get_track_simple
from .crawler_util import get_user_agent

logger = logging.getLogger(__name__)


class SliderCaptchaSolver:
    """基于 OpenCV 的通用滑块验证码求解器。"""

    def __init__(self, temp_dir: str = "temp_captcha") -> None:
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # ── 检测 ──────────────────────────────────────────────────────

    @staticmethod
    async def detect_captcha(page) -> bool:
        """检测当前页面是否包含验证码。

        通过页面标题和 DOM 内容中的关键词判断。
        """
        try:
            title = await page.title()
            lower_title = title.lower()
            captcha_keywords = [
                "captcha", "verify", "verification",
                "人机验证", "验证码", "滑块验证",
                "slider", "slide to verify", "请完成安全验证",
                "are you a robot", "press and hold",
            ]
            if any(kw in lower_title for kw in captcha_keywords):
                return True

            # 检查 body 文本（前 3000 字符）
            try:
                body_text = await page.evaluate(
                    "() => document.body ? document.body.innerText.substring(0, 3000).toLowerCase() : ''"
                )
                if body_text and any(kw in body_text for kw in captcha_keywords):
                    return True
            except Exception:
                pass

            return False
        except Exception as e:
            logger.debug("[CaptchaSolver] 验证码检测异常: %s", e)
            return False

    # ── 求解 ──────────────────────────────────────────────────────

    async def solve_slider(
        self,
        page,
        gap_selector: str,
        bg_selector: str,
        slider_selector: str = "",
    ) -> bool:
        """完整的滑块验证码求解流水线。

        Args:
            page: Playwright Page 对象
            gap_selector: 缺口图片的 CSS 选择器
            bg_selector: 背景图片的 CSS 选择器
            slider_selector: 滑块按钮的 CSS 选择器（空则尝试自动查找）

        Returns:
            True 表示求解并拖拽成功，False 表示失败
        """
        try:
            # 1. 获取缺口和背景图片的 URL
            gap_url = await page.evaluate(
                f"() => document.querySelector('{gap_selector}')?.src || ''"
            )
            bg_url = await page.evaluate(
                f"() => document.querySelector('{bg_selector}')?.src || ''"
            )
            if not gap_url or not bg_url:
                logger.warning("[CaptchaSolver] 无法获取验证码图片 URL")
                return False

            # 2. 下载图片
            gap_path = str(self.temp_dir / "captcha_gap.png")
            bg_path = str(self.temp_dir / "captcha_bg.png")
            if not await self._download_image(gap_url, gap_path):
                return False
            if not await self._download_image(bg_url, bg_path):
                return False

            # 3. 计算缺口距离（OpenCV 是 CPU 密集型，offload 到线程）
            distance = await asyncio.to_thread(self._compute_gap_distance, gap_path, bg_path)
            if distance <= 0:
                logger.warning("[CaptchaSolver] 无法计算缺口距离")
                return False

            logger.info("[CaptchaSolver] 缺口距离: %dpx", distance)

            # 4. 生成人类化拖拽轨迹
            track = get_track_simple(distance)

            # 5. 执行拖拽
            if not slider_selector:
                # 尝试常见滑块选择器
                slider_selector = await self._find_slider_selector(page)
                if not slider_selector:
                    logger.warning("[CaptchaSolver] 无法找到滑块元素")
                    return False

            await self._perform_drag(page, slider_selector, track)
            logger.info("[CaptchaSolver] 滑块拖拽完成，等待验证结果...")

            # 6. 短暂等待，让页面反应
            await asyncio.sleep(1.5)

            # 7. 检查验证码是否消失
            still_present = await self.detect_captcha(page)
            if still_present:
                logger.warning("[CaptchaSolver] 验证码可能未通过")
                return False

            return True

        except Exception as e:
            logger.error("[CaptchaSolver] 求解过程出错: %s", e)
            return False

    # ── 内部方法 ──────────────────────────────────────────────────

    def _compute_gap_distance(self, gap_path: str, bg_path: str) -> int:
        """OpenCV 边缘检测 + 模板匹配，返回滑块缺口 x 坐标。"""
        try:
            gap = cv2.imread(gap_path, cv2.IMREAD_COLOR)
            bg = cv2.imread(bg_path, cv2.IMREAD_COLOR)
            if gap is None or bg is None:
                return -1

            # 清除缺口图白边
            gap_cleaned = self._clear_white_border(gap)

            # 转灰度 + 边缘检测
            gap_gray = cv2.cvtColor(gap_cleaned, cv2.COLOR_BGR2GRAY)
            gap_edges = cv2.Canny(gap_gray, 100, 200)

            bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
            bg_edges = cv2.Canny(bg_gray, 100, 200)

            # 模板匹配
            result = cv2.matchTemplate(bg_edges, gap_edges, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val < 0.3:
                logger.warning("[CaptchaSolver] 模板匹配置信度过低: %.3f", max_val)

            return max_loc[0]  # x 坐标
        except Exception as e:
            logger.error("[CaptchaSolver] 图像处理失败: %s", e)
            return -1

    @staticmethod
    def _clear_white_border(img: np.ndarray) -> np.ndarray:
        """清除图像白边，提取有效区域。"""
        non_white = np.any(img != [255, 255, 255], axis=2)
        rows = np.any(non_white, axis=1)
        cols = np.any(non_white, axis=0)
        if not rows.any() or not cols.any():
            return img
        min_y, max_y = np.where(rows)[0][[0, -1]]
        min_x, max_x = np.where(cols)[0][[0, -1]]
        if max_x > min_x and max_y > min_y:
            return img[min_y:max_y + 1, min_x:max_x + 1]
        return img

    async def _find_slider_selector(self, page) -> str:
        """自动查找页面中的滑块按钮选择器。"""
        candidates = [
            ".slider", ".slide", ".slider-btn", ".slide-btn",
            ".slider_button", ".slide_button", ".sliderButton",
            "[class*='slider']", "[class*='slide']",
            ".box-static", ".boxStatic",
            ".verify-slider", ".captcha-slider",
        ]
        for sel in candidates:
            try:
                exists = await page.evaluate(
                    f"() => !!document.querySelector('{sel}')"
                )
                if exists:
                    return sel
            except Exception:
                continue
        return ""

    async def _perform_drag(self, page, slider_selector: str, track: List[int]) -> None:
        """在页面上执行滑块拖拽操作，轨迹使用人类化的加速-减速模式。"""
        try:
            slider = await page.query_selector(slider_selector)
            if not slider:
                logger.warning("[CaptchaSolver] 找不到滑块元素: %s", slider_selector)
                return

            box = await slider.bounding_box()
            if not box:
                return

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2

            # 按下鼠标
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()

            # 按轨迹移动
            for step in track:
                if step == 0:
                    continue
                start_x += step
                await page.mouse.move(start_x, start_y, steps=1)
                await asyncio.sleep(0.005)  # 5ms per step

            # 松开鼠标
            await asyncio.sleep(0.05)
            await page.mouse.up()

        except Exception as e:
            logger.error("[CaptchaSolver] 拖拽操作失败: %s", e)

    @staticmethod
    async def _download_image(url: str, save_path: str) -> bool:
        """下载验证码图片到本地。"""
        try:
            headers = {
                "User-Agent": get_user_agent(),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    Path(save_path).write_bytes(resp.content)
                    return True
                return False
        except Exception as e:
            logger.debug("[CaptchaSolver] 图片下载失败 %s: %s", url, e)
            return False
