#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号数据接入（P0-1 数据复盘）
====================================
封装微信公众号开放平台 API：
- get_access_token(): 凭证换取 access_token（内存缓存 7000s）
- fetch_fans_total(): 当前关注者总数（/cgi-bin/user/get）
- fetch_fans_summary(date): 当日粉丝增减（datacube/getusersummary，需接口权限）
- fetch_article_summary(date): 当日图文数据（datacube/getarticlesummary，需接口权限）

所有调用失败不抛异常，返回 (data|None, error_msg) 由调用方展示。
权限/白名单问题（40164 / 48001）会原样带回错误信息。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent

# 独立使用 / 被 app.py import 前先加载 .env（与 cat_shop 行为一致）
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env", override=False)
except Exception:
    pass

_TOKEN = {"at": None, "expires_at": 0.0}


def get_access_token(force=False):
    """返回 (access_token, error)。token 内存缓存，过期自动刷新。"""
    now = time.time()
    if not force and _TOKEN["at"] and _TOKEN["expires_at"] > now + 300:
        return _TOKEN["at"], None
    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        return None, "未配置 WECHAT_APPID / WECHAT_APPSECRET（.env）"
    try:
        url = ("https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential"
               f"&appid={urllib.parse.quote(appid)}&secret={urllib.parse.quote(secret)}")
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("errcode"):
            return None, f"{data.get('errcode')}: {data.get('errmsg')}"
        _TOKEN["at"] = data["access_token"]
        _TOKEN["expires_at"] = now + int(data.get("expires_in", 7200)) - 300
        return _TOKEN["at"], None
    except Exception as e:
        return None, str(e)


def _api_get(path, params=None):
    at, err = get_access_token()
    if err:
        return None, err
    url = f"https://api.weixin.qq.com{path}?access_token={at}"
    if params:
        url += "&" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("errcode"):
            return None, f"{data.get('errcode')}: {data.get('errmsg')}"
        return data, None
    except Exception as e:
        return None, str(e)


def _api_post(path, payload):
    at, err = get_access_token()
    if err:
        return None, err
    url = f"https://api.weixin.qq.com{path}?access_token={at}"
    try:
        # 修复草稿箱乱码：ensure_ascii=False 让中文直接以 UTF-8 出现在 body
        # （draft/add 收到 \uXXXX JSON 后未做解码就存储，会导致 content/title 显示为 \u7761 转义串）
        req = urllib.request.Request(url,
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("errcode"):
            return None, f"{data.get('errcode')}: {data.get('errmsg')}"
        return data, None
    except Exception as e:
        return None, str(e)


def fetch_fans_total():
    """当前关注者总数。返回 (total, error)。"""
    data, err = _api_get("/cgi-bin/user/get", {"next_openid": ""})
    if err:
        return None, err
    return data.get("total"), None


def fetch_fans_summary(date):
    """当日粉丝增减数据。返回 (data, error)。date: YYYY-MM-DD"""
    return _api_post("/datacube/getusersummary",
                     {"begin_date": date, "end_date": date})


def fetch_article_summary(date):
    """当日图文群发数据。返回 (data, error)。date: YYYY-MM-DD"""
    return _api_post("/datacube/getarticlesummary",
                     {"begin_date": date, "end_date": date})


def api_health():
    """接入健康检查。返回 (ok, detail)。"""
    at, err = get_access_token(force=True)
    if err:
        return False, f"token 获取失败：{err}"
    total, err2 = fetch_fans_total()
    if err2:
        if "48001" in err2:
            # 未认证订阅号：统计/用户数据不可用，但素材与草稿箱接口实测可用，可直接写公众号草稿箱。
            # 因此这里视为「可推送」健康态（ok=True），否则 push_wechat_draft 会误判不可用而跳过推送。
            return True, ("API 已连通（token 有效）。账号为未认证订阅号：统计/用户数据接口不可用，"
                          "但素材与草稿箱接口可用——已支持把最终文稿直写公众号草稿箱。"
                          "阅读/在看/分享/粉丝数据请从公众号后台统计页手动录入。")
        return False, f"token 有效但接口受限：{err2}"
    return True, f"token 有效，当前关注者总数 {total}"


# ===== 草稿箱直写能力（未认证订阅号实测可用） =====
def _multipart(url, fields=None, files=None):
    """通用 multipart/form-data 请求。files: {name: (filename, bytes, mime)}。返回 (json, err)。"""
    import io
    import uuid
    at, err = get_access_token()
    if err:
        return None, err
    boundary = "----wb" + uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in (fields or {}).items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.write(str(v).encode("utf-8"))
        body.write(b"\r\n")
    for name, (fname, data, mime) in (files or {}).items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode())
        body.write(f"Content-Type: {mime}\r\n\r\n".encode())
        body.write(data)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    full = f"{url}?access_token={at}"
    req = urllib.request.Request(full, data=body.getvalue(), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("errcode"):
            return None, f"{data.get('errcode')}: {data.get('errmsg')}"
        return data, None
    except Exception as e:
        return None, str(e)


def add_material_image(image_bytes, filename="cover.png"):
    """上传永久图片素材（封面用）。返回 (media_id, url, err)。"""
    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    data, err = _multipart("https://api.weixin.qq.com/cgi-bin/material/add_material",
                           fields={"type": "image"},
                           files={"media": (filename, image_bytes, mime)})
    if err:
        return None, None, err
    return data.get("media_id"), data.get("url"), None


def upload_img_for_article(image_bytes, filename="inline.png"):
    """上传图文内图片（media/uploadimg），返回微信可用的图片 URL。"""
    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    data, err = _multipart("https://api.weixin.qq.com/cgi-bin/media/uploadimg",
                           files={"media": (filename, image_bytes, mime)})
    if err:
        return None, err
    return data.get("url"), None


def strip_cover_img(content_html):
    """去掉正文头图 img（data-cover="1"）。用于头图换装失败后的降级：
    file:// 地址在微信内无法显示，删掉比留一张裂图体面。"""
    return re.sub(r'<img[^>]*data-cover="1"[^>]*>\s*', "", content_html)


def replace_local_images(content_html):
    """P3 文中配图推送前换装：把正文里所有本地 file:// 图片上传微信素材
    （media/uploadimg，图文内可显示）并替换 src。
    返回 (new_html, replaced, err)；单张失败跳过该张（留本地 src，不阻塞推送），
    仅在完全无法解析时返回 err。"""
    import urllib.parse
    from pathlib import Path as _P

    pattern = re.compile(r'src="(file:///[^"]+)"')
    if not pattern.search(content_html):
        return content_html, 0, None

    replaced = 0

    def _repl(m):
        nonlocal replaced
        raw = urllib.parse.unquote(m.group(1)[len("file:///"):])
        p = _P(raw)
        try:
            data = p.read_bytes()
        except OSError:
            return m.group(0)  # 文件丢失 → 保持原样（推送侧无法兜底，宁缺毋错）
        url, err = upload_img_for_article(data, p.name or "inline.png")
        if err or not url:
            return m.group(0)
        replaced += 1
        return f'src="{url}"'

    new_html = pattern.sub(_repl, content_html)
    return new_html, replaced, None


def swap_cover_url(content_html, cover_bytes, filename="cover.png"):
    """头图推送前的两步换装（v4.2 M1：每篇唯一配图）：
    ① 正文头图 img 的 src 换成微信 URL（media/uploadimg，图文内可显示）
    ② 同一张图上传永久素材拿 thumb_media_id（草稿封面）
    返回 (new_html, thumb_media_id, note, err)；err 非空时其余保持原值。
    正文无头图标记（LLM 回退渲染的存量稿）时仅做 ②，html 原样返回。"""
    url, err = upload_img_for_article(cover_bytes, filename)
    if err:
        return content_html, None, "", f"正文头图上传失败: {err}"
    thumb_id, _url, err2 = add_material_image(cover_bytes, filename)
    if err2:
        return content_html, None, "", f"封面素材上传失败: {err2}"

    def _repl(m):
        tag = re.sub(r'\s*data-cover="1"', "", m.group(0))
        return re.sub(r'src="[^"]*"', f'src="{url}"', tag, count=1)

    new_html = re.sub(r'<img[^>]*data-cover="1"[^>]*>', _repl, content_html)
    return new_html, thumb_id, "生成头图", None


def list_image_materials(offset=0, count=10):
    """列出永久图片素材。返回 (items, err)。items: [{media_id, name, url, update_time}]"""
    data, err = _api_post("/cgi-bin/material/batchget_material",
                          {"type": "image", "offset": offset, "count": count})
    if err:
        return None, err
    return data.get("item", []), None


# 公众号作者名（历史曾硬编码为“掌柜”，导致草稿箱作者“猫”字丢失，统一为常量）
WECHAT_AUTHOR = "作者"


def draft_add(title, content_html, thumb_media_id, author=WECHAT_AUTHOR, digest=""):
    """新增草稿到公众号草稿箱。返回 (media_id, err)。
    author 最长 8 字节（4 个汉字）；content_html 为图文 HTML（内联样式可保留）。"""
    payload = {"articles": [{
        "title": title[:64],
        "author": author[:8],
        "content": content_html,
        "digest": digest[:120],
        "content_source_url": "",
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
        "thumb_media_id": thumb_media_id,
    }]}
    data, err = _api_post("/cgi-bin/draft/add", payload)
    if err:
        return None, err
    return data.get("media_id"), None


def draft_delete(media_id):
    """删除草稿（删旧建新策略用）。返回 (ok, err)。"""
    _, err = _api_post("/cgi-bin/draft/delete", {"media_id": media_id})
    return not err, err


def draft_update(media_id, title, content_html, thumb_media_id, author=WECHAT_AUTHOR, digest=""):
    """更新已有草稿（P-04）。返回 (media_id, err)。media_id 为草稿 media_id，index=0 更新第一篇。

    ⚠️ 已知坑（2026-10-08 实测）：draft/get 返回的 content 被微信改写
    （img 的 src → data-src 懒加载形态），原样回传必报 47001 data format
    error——即使用全新 content 走本接口也可能 47001。修正草稿请优先用
    「删旧建新」：draft_delete(old) + draft_add(new)。"""
    payload = {"media_id": media_id, "index": 0, "articles": [{
        "title": title[:64], "author": author[:8], "content": content_html,
        "digest": digest[:120], "content_source_url": "",
        "need_open_comment": 0, "only_fans_can_comment": 0,
        "thumb_media_id": thumb_media_id,
    }]}
    data, err = _api_post("/cgi-bin/draft/update", payload)
    if err:
        return None, err
    return media_id, None


def draft_count():
    data, err = _api_post("/cgi-bin/draft/count", {})
    if err:
        return None, err
    return data.get("total_count"), None
