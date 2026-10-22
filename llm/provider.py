#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 适配层（三期-0 · v4.0 自根目录 llm_provider.py 平移至 llm/provider.py）
==========================================================================
把模型调用抽象为独立可替换模块。核心业务（cat_shop.call_llm / exporter）只通过
get_provider() 拿到统一接口，不感知具体服务商。

切换服务商只需：
  1. 在此文件新增一个 LLMProvider 子类（实现 chat()）
  2. 在 .env 设 LLM_PROVIDER=<新provider>
  3. 如有新计费规则，在子类 cost() 里返回 (input_cny, output_cny)

当前实现：DeepSeekProvider（OpenAI 兼容协议）
参考费率（2026-08，deepseek-chat）：输入 ¥0.001/1K tokens、输出 ¥0.002/1K tokens
"""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

import sys
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent  # llm/ 下一级 → 锚定项目根
COST_LOG = SCRIPT_DIR / "data" / "llm_cost.jsonl"

# 限流锁（跨 provider 共享，保证多线程评分时节流）
_LOCK = threading.Lock()
_LAST_CALL_TS = 0.0


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


class LLMProvider:
    """统一接口。子类实现 chat() 返回 (content, usage_dict)。"""

    name = "base"

    def __init__(self, api_key, api_base, model):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model

    def chat(self, prompt, temperature=0, max_tokens=2000, json_mode=True):
        """返回 (content: str|dict, usage: {prompt_tokens, completion_tokens, total_tokens})。"""
        raise NotImplementedError

    def cost(self, usage):
        """返回 (input_cny, output_cny)。默认 0；子类按实际费率计算。"""
        return 0.0, 0.0


class DeepSeekProvider(LLMProvider):
    """DeepSeek（OpenAI 兼容 /chat/completions 协议）。"""

    name = "deepseek"
    # 2026-08 deepseek-chat 官方费率（CNY / 1K tokens）
    INPUT_RATE = 0.001
    OUTPUT_RATE = 0.002

    def chat(self, prompt, temperature=0, max_tokens=2000, json_mode=True):
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": temperature, "max_tokens": max_tokens,
                   "stream": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return content, usage

    def cost(self, usage):
        pt = (usage.get("prompt_tokens") or 0) / 1000.0
        ct = (usage.get("completion_tokens") or 0) / 1000.0
        return round(pt * self.INPUT_RATE, 6), round(ct * self.OUTPUT_RATE, 6)


# ===== Provider 注册表（新增服务商在此登记） =====
_REGISTRY = {"deepseek": DeepSeekProvider}


def get_provider():
    """根据 .env 返回单例 provider。LLM_PROVIDER 缺省 deepseek。"""
    name = os.environ.get("LLM_PROVIDER") or "deepseek"
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 LLM_PROVIDER={name}，已注册: {list(_REGISTRY)}")
    api_key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    api_base = (os.environ.get("LLM_API_BASE")
                or os.environ.get("DEEPSEEK_API_BASE")
                or "https://api.deepseek.com/v1").strip()
    model = (os.environ.get("LLM_MODEL")
             or os.environ.get("DEEPSEEK_MODEL")
             or "deepseek-chat").strip()
    if not api_key:
        raise EnvironmentError("未设置 LLM_API_KEY（跑 `python cat_shop.py setup`）")
    return cls(api_key=api_key, api_base=api_base, model=model)


# ===== 成本记录（三期-1） =====
def log_cost(provider_name, model, usage, module, cost_in, cost_out, ok=True, error=""):
    """追加一条成本记录到 data/llm_cost.jsonl。失败调用也记录（cost=0）。"""
    try:
        COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now_iso(), "date": _now_iso()[:10],
            "provider": provider_name, "model": model,
            "module": module,  # collect / score / write / analyze / redline_semantic / mp_export
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_in_cny": round(cost_in, 6),
            "cost_out_cny": round(cost_out, 6),
            "cost_total_cny": round(cost_in + cost_out, 6),
            "ok": ok, "error": error[:200],
        }
        with COST_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 成本日志不影响主流程


def throttle(min_interval=0.5):
    """限流：两次调用间至少 min_interval 秒。

    锁内只做槽位预订（原子推进 _LAST_CALL_TS），等待在锁外进行——
    持锁 sleep 会把进程内所有 LLM 调用串成一列， judge/reflect/工具调用互相排队。
    """
    global _LAST_CALL_TS
    with _LOCK:
        now = time.time()
        wait = (_LAST_CALL_TS + min_interval) - now
        _LAST_CALL_TS = max(now, _LAST_CALL_TS + min_interval)
    if wait > 0:
        time.sleep(wait)


def call(provider, prompt, temperature=0, max_tokens=2000, json_mode=True,
         module="unknown", retries=3, base_delay=2.0):
    """统一调用入口：限流 → 调用 → 记成本 → 解析。返回与原 call_llm 一致。
    json_mode=True 返回 dict；False 返回 str。"""
    throttle()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            content, usage = provider.chat(prompt, temperature=temperature,
                                           max_tokens=max_tokens, json_mode=json_mode)
            in_cny, out_cny = provider.cost(usage)
            log_cost(provider.name, provider.model, usage, module, in_cny, out_cny, ok=True)
            if not json_mode:
                return content
            data = json.loads(content)
            if isinstance(data, dict) and data:
                return data
            last_err = ValueError(f"JSON 内容为空或结构非法: {content[:120]!r}")
        except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            log_cost(provider.name, provider.model, {}, module, 0, 0, ok=False, error=str(e))
            if attempt < retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"  [WARN] LLM 调用失败(第 {attempt}/{retries} 次): {e}，{delay:.0f}s 后重试",
                      file=__import__("sys").stderr)
                time.sleep(delay)
    raise RuntimeError(f"LLM 调用在 {retries} 次尝试后仍失败: {last_err}")
