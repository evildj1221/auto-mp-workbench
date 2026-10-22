#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/smoke_api.py · 工作台 API 冒烟 + 并发压测（可重复执行，零 LLM 成本）。

用法：
  python scripts/smoke_api.py                          # 默认 127.0.0.1:8765 + 今天
  python scripts/smoke_api.py --base http://127.0.0.1:8766 --date 2026-09-26 \
         --rounds 5 --concurrency 4

内容：
  1) 串行延迟：核心 GET 接口各 3 次，报最优/最差
  2) 并发压测：混合接口 × concurrency 并发 × rounds 轮，报 p50/p95/最大值与失败数

判定（exit code）：串行全部 < 500ms 且 并发失败数=0 且 p95 < 2000ms → PASS(0)，否则 FAIL(1)。
服务需已启动（重启工作台服务.bat / python -m uvicorn backend.app.main:app）。
"""
import argparse
import concurrent.futures
import statistics
import sys
import time

import requests

ENDPOINTS = [
    ("topics", "/api/topics/{date}"),
    ("drafts", "/api/drafts/{date}"),
    ("export", "/api/export/{date}"),
    ("dashboard", "/api/dashboard/{date}"),
    ("chat-brief", "/api/chat/brief?date={date}"),
    ("chat-personas", "/api/chat/personas"),
    ("agent-status", "/api/agent/{date}/status"),
    ("review", "/api/review/{date}"),
    ("archive", "/api/archive"),
    ("settings", "/api/settings"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--rounds", type=int, default=5, help="并发轮数")
    ap.add_argument("--concurrency", type=int, default=4, help="并发线程数")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    date = args.date

    urls = [base + e[1].format(date=date) for e in ENDPOINTS]

    print(f"== 冒烟目标 {base} · 日期 {date} ==")
    # 1) 串行延迟
    serial_ok = True
    for (name, path), url in zip(ENDPOINTS, urls):
        ts = []
        for _ in range(3):
            t0 = time.time()
            try:
                r = requests.get(url, timeout=30)
                ts.append((time.time() - t0) * 1000 if r.status_code == 200 else -1)
            except Exception:
                ts.append(-1)
        ok = min(ts) > 0 and max(ts) < 500
        serial_ok = serial_ok and ok
        print(f"  串行 {name:14s} best {min(ts):6.0f}ms  worst {max(ts):6.0f}ms  "
              f"{'OK' if ok else '!! 慢/失败'}")

    # 2) 并发压测
    lat, fails = [], 0
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as ex:
        for _ in range(args.rounds):
            for r in ex.map(hit, urls):
                if r is None:
                    fails += 1
                else:
                    lat.append(r)
    wall = time.time() - t0
    lat.sort()
    p50 = lat[len(lat) // 2] * 1000 if lat else -1
    p95 = lat[int(len(lat) * 0.95)] * 1000 if lat else -1
    mx = lat[-1] * 1000 if lat else -1
    total = len(urls) * args.rounds
    print(f"== 并发 {args.concurrency} 线程 × {args.rounds} 轮 × {len(urls)} 接口 = {total} 请求，"
          f"{wall:.1f}s ==")
    print(f"  p50 {p50:.0f}ms  p95 {p95:.0f}ms  max {mx:.0f}ms  失败 {fails}")

    passed = serial_ok and fails == 0 and p95 < 2000
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


def hit(url: str):
    """单次请求，成功返回延迟秒数，失败返回 None。"""
    t0 = time.time()
    try:
        r = requests.get(url, timeout=30)
        return (time.time() - t0) if r.status_code == 200 else None
    except Exception:
        return None


if __name__ == "__main__":
    main()
