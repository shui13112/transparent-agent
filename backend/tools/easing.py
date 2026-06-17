"""人类化鼠标移动轨迹生成（缓动函数）。

从 MediaCrawler 移植，用于滑块验证码的拖拽轨迹模拟。
"""

from __future__ import annotations

import random
from typing import List, Tuple

import numpy as np


def ease_in_quad(x: float) -> float:
    return x * x


def ease_out_quad(x: float) -> float:
    return 1 - (1 - x) * (1 - x)


def ease_out_quart(x: float) -> float:
    return 1 - pow(1 - x, 4)


def ease_out_expo(x: float) -> float:
    if x == 1:
        return 1
    return 1 - pow(2, -10 * x)


def ease_out_bounce(x: float) -> float:
    n1 = 7.5625
    d1 = 2.75
    if x < 1 / d1:
        return n1 * x * x
    elif x < 2 / d1:
        x -= 1.5 / d1
        return n1 * x * x + 0.75
    elif x < 2.5 / d1:
        x -= 2.25 / d1
        return n1 * x * x + 0.9375
    else:
        x -= 2.625 / d1
        return n1 * x * x + 0.984375


def get_tracks(
    distance: float,
    seconds: float = 2,
    ease_func: str = "ease_out_expo",
) -> Tuple[List[int], List[int]]:
    """生成人类化鼠标拖拽轨迹。

    Args:
        distance: 滑块需要移动的总距离（像素）
        seconds: 拖拽持续时间（秒）
        ease_func: 缓动函数名

    Returns:
        (offsets, tracks) — offsets 是累计位移，tracks 是每步增量
    """
    ease = globals()[ease_func]
    offsets = [0]
    tracks: List[int] = [0]
    for t in np.arange(0.0, seconds, 0.1):
        offset = round(ease(t / seconds) * distance)
        tracks.append(offset - offsets[-1])
        offsets.append(offset)
    return offsets, tracks


def get_track_simple(distance: float) -> List[int]:
    """简化的加速-减速轨迹（不依赖 numpy）。

    前半段加速（a=4），后半段减速（a=-3），避免匀速被检测。
    """
    track: List[int] = []
    current = 0.0
    mid = distance * 4 / 5
    t = 0.2
    v = 1.0

    while current < distance:
        if current < mid:
            a = 4.0
        else:
            a = -3.0
        v0 = v
        v = v0 + a * t
        move = v0 * t + 0.5 * a * t * t
        current += move
        track.append(round(move))

    # 在起点添加微量随机抖动，模拟人类手抖
    if len(track) > 3:
        track[0] += random.randint(-1, 1)
        track[1] += random.randint(-1, 1)
    return track
