#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信推送链路体检：IP / AppSecret / 上传 / 草稿箱 四步逐项诊断。

用法：python scripts/wechat_doctor.py
输出每步 PASS/FAIL + 修复指引；第 1-3 步 PASS 后可直接 --push 一键推草稿箱。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cat_shop  # noqa: F401  (.env)
import requests
from core import wechat


def main():
    ok = True
    print("=== 微信推送链路体检 ===")

    # 1) 出口 IP
    ip = ""
    for url in ("https://api.ipify.org?format=json", "https://api.ip.sb/ip",
                "http://ifconfig.me/ip"):
        try:
            r = requests.get(url, timeout=8)
            ip = (r.json().get("ip") if "json" in r.headers.get("content-type", "")
                  else r.text).strip()
            if ip:
                break
        except Exception:
            continue
    print(f"1) 出口 IP: {ip or '查询失败（不影响微信调用，仅供白名单核对）'}")
    print(f"   → 确认微信后台「IP 白名单」里有 {ip}（当前出口经常变）")

    # 2) access_token（核心：40125=secret错，40164=IP不在白名单）
    tok, err = wechat.get_access_token(force=True)
    if tok:
        print(f"2) access_token: PASS（secret 与白名单均有效）")
    else:
        ok = False
        e = str(err)
        print(f"2) access_token: FAIL → {e}")
        if "40125" in e:
            print("   → AppSecret 不对：公众平台→设置与开发→基本配置→AppSecret【重置】")
            print("     （管理员扫码，新值只显示一次）→ 更新 .env 的 WECHAT_APPSECRET")
        elif "40164" in e or "invalid ip" in e.lower():
            print(f"   → IP 不在白名单：把 {ip} 加进公众平台「IP 白名单」")
        elif "40013" in e:
            print("   → AppID 不对：核对 .env 的 WECHAT_APPID")
        else:
            print("   → 按错误码对照公众平台文档排查")

    # 3) 上传测试（1x1 png）
    if ok:
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
               b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
               b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
        url, err = wechat.upload_img_for_article(png, "doctor.png")
        if url:
            print(f"3) 图片上传: PASS（{url[:60]}…）")
        else:
            ok = False
            print(f"3) 图片上传: FAIL → {err}")

    print(f"\n结论：{'全部通，可推草稿箱（python scripts/wechat_doctor.py --push）' if ok else '存在卡点，按上面指引修复后重跑'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--push" in sys.argv:
        from scripts.push_1008 import push
        sys.exit(0 if push() else 1)
    sys.exit(main())
