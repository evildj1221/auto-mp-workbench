#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片生成适配层（v4.2 M1）· 复制 llm/provider.py 的适配层模式。
==========================================================================
头图生成服务抽象：业务（tools/image_tools.py）只通过 get_image_provider()
拿到统一接口 generate(prompt, size) -> (png_bytes, usage)，不感知具体服务商。

切换服务商只需：
  1. 在此文件新增一个 ImageProvider 子类（实现 generate()）
  2. 在 .env 设 IMAGE_PROVIDER=<新provider>
  3. 计费规则在子类 price() 里返回 (cny, usage_dict)

当前实现：
  - ArkProvider（火山方舟 · OpenAI images/generations 兼容协议，Seedream / 即梦系列模型）
  - MiniMaxImageProvider（MiniMax image-01 · /v1/image_generation，base64 返回，¥0.025/张）
无 key 时 get_image_provider() 抛错，
工具层捕获后优雅降级（不阻塞写稿与发布链路）。
"""
import base64
import os
from pathlib import Path

import requests

from llm.provider import log_cost

# 头图规格：2.35:1（公众号封面原生比例；视觉设计系统 v3 第 6.1 节"21:9 / 2K / 2.35x1"）
# 2048x872：宽达 2K 档、总像素 179 万，同时满足 MiniMax 约束（8 的倍数、单边 ≤2048、≤200 万）
COVER_SIZE = "2048x872"


class ImageProvider:
    """统一接口。子类实现 generate() 返回 (image_bytes, usage_dict)。"""

    name = "base"

    def __init__(self, api_key, api_base, model):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model

    def generate(self, prompt, size=COVER_SIZE, reference=None):
        """返回 (image_bytes: bytes, usage: {images: int})。
        reference: 可选参考图 bytes（人设/风格一致性），不支持的 provider 忽略。"""
        raise NotImplementedError

    def price(self, usage):
        """返回 (cny, usage)。默认按张计费由子类实现。"""
        return 0.0, usage


class ArkProvider(ImageProvider):
    """火山方舟（OpenAI images/generations 兼容协议 · Seedream/即梦）。

    参考费率（2026-09，doubao-seedream-4-0 ≤2048px）：约 ¥0.06/张，
    可用 IMAGE_PRICE_CNY 覆盖。响应取 b64_json 落盘（URL 有时效，不依赖）。
    """

    name = "ark"

    def generate(self, prompt, size=COVER_SIZE):
        payload = {"model": self.model, "prompt": prompt, "size": size,
                   "response_format": "b64_json", "stream": False}
        r = requests.post(
            f"{self.api_base}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        item = (data.get("data") or [{}])[0]
        b64 = item.get("b64_json") or ""
        if not b64:
            url = item.get("url") or ""
            if not url:
                raise ValueError(f"生图响应无图片内容: {str(data)[:200]}")
            img = requests.get(url, timeout=120)
            img.raise_for_status()
            return img.content, {"images": 1}
        return base64.b64decode(b64), {"images": 1}

    def price(self, usage):
        cny = float(os.environ.get("IMAGE_PRICE_CNY") or 0.06)
        return round(cny * (usage.get("images") or 1), 6), usage


class MiniMaxImageProvider(ImageProvider):
    """MiniMax image-01（POST /v1/image_generation，Bearer 认证）。

    参考费率（2026-09，image-01/image-01-live）：¥0.025/张，限频 10 RPM。
    base64 返回（url 仅 24h 有效，不依赖）；size 转 width/height
    （须为 8 的倍数、[512,2048]、总像素 ≤200 万）。10 RPM 限频：
    429 时退避重试至多 2 次。
    """

    name = "minimax"
    _RETRY_DELAYS = (8, 15)  # 429 退避秒数（10 RPM → 单张最快 6s/次）

    def generate(self, prompt, size=COVER_SIZE, reference=None):
        width, height = self._parse_size(size)
        payload = {"model": self.model, "prompt": prompt[:1500],
                   "width": width, "height": height,
                   "response_format": "base64", "n": 1}
        if reference:  # subject_reference 人设/风格参考（单图，base64 data URI）
            payload["subject_reference"] = [{
                "type": "character",
                "image_file": "data:image/jpeg;base64,"
                              + base64.b64encode(reference).decode()}]
        return self._request(payload)

    def _request(self, payload):
        import time
        last_err = None
        for attempt, delay in enumerate((0,) + self._RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            r = requests.post(
                f"{self.api_base}/v1/image_generation",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=180)
            if r.status_code == 429 and attempt < len(self._RETRY_DELAYS):
                last_err = f"429 rate limit（第 {attempt + 1} 次退避）"
                continue
            r.raise_for_status()
            data = r.json()
            base_resp = data.get("base_resp") or {}
            if base_resp.get("status_code") not in (0, None):
                raise ValueError(f"MiniMax 生图失败: {base_resp}")
            b64_list = ((data.get("data") or {}).get("image_base64")) or []
            if not b64_list:
                raise ValueError(f"生图响应无图片内容: {str(data)[:200]}")
            return base64.b64decode(b64_list[0]), {"images": 1}
        raise RuntimeError(f"MiniMax 生图限频重试耗尽: {last_err}")

    @staticmethod
    def _parse_size(size):
        w, h = str(size).lower().split("x")
        w, h = int(w), int(h)
        for v in (w, h):
            if not (512 <= v <= 2048) or v % 8:
                raise ValueError(
                    f"MiniMax 要求宽高为 8 的倍数且在 [512,2048]，收到 {size}")
        if w * h > 2_000_000:
            raise ValueError(f"总像素超 200 万上限: {size}={w * h}")
        return w, h

    def price(self, usage):
        cny = float(os.environ.get("IMAGE_PRICE_CNY") or 0.025)
        return round(cny * (usage.get("images") or 1), 6), usage


# ===== Provider 注册表（新增服务商在此登记） =====
_REGISTRY = {"ark": ArkProvider, "minimax": MiniMaxImageProvider}


def image_configured() -> bool:
    """是否已配置生图 API（未配置时工具层走降级路径）。"""
    return bool((os.environ.get("IMAGE_API_KEY") or "").strip())


def get_image_provider():
    """根据 .env 返回图片 provider。缺省 ark（火山方舟）。"""
    name = os.environ.get("IMAGE_PROVIDER") or "ark"
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 IMAGE_PROVIDER={name}，已注册: {list(_REGISTRY)}")
    api_key = (os.environ.get("IMAGE_API_KEY") or "").strip()
    api_base = (os.environ.get("IMAGE_API_BASE")
                or "https://ark.cn-beijing.volces.com/api/v3").strip()
    model = (os.environ.get("IMAGE_MODEL") or "doubao-seedream-4-0").strip()
    if not api_key:
        raise EnvironmentError(
            "未设置 IMAGE_API_KEY（火山方舟控制台获取，写入 .env 后重启）")
    return cls(api_key=api_key, api_base=api_base, model=model)


def generate_image(prompt, size=COVER_SIZE, module="image_cover", reference=None):
    """统一调用入口：调用 → 计价 → 记成本账本 → 返回图片字节。
    reference: 可选参考图 bytes（人设/风格一致性；不支持参考的 provider 忽略）。

    失败不重试（生图幂等重试浪费成本，由调用方决定是否再试一次），
    失败也记账（cost=0, ok=False），保证成本账本可观测。
    """
    provider = get_image_provider()
    try:
        img_bytes, usage = provider.generate(prompt, size=size, reference=reference)
        cny, usage = provider.price(usage)
        log_cost(provider.name, provider.model, usage, module, 0.0, cny, ok=True)
        return img_bytes
    except Exception as e:
        log_cost(provider.name, provider.model, {}, module, 0.0, 0.0,
                 ok=False, error=str(e))
        raise
