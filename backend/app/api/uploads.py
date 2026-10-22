#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/uploads.py · 用户素材上传（P3）。

- 图片（png/jpg/webp）→ 存档 + 可作生图参考（首张自动作为 subject_reference）
- 文稿（md/txt/docx）→ 抽取文本存入清单，写稿时注入 writer prompt 的「用户素材」段
存储：data/uploads/{date}_{slot}/（slot 为空用 date）+ manifest.json 清单
"""
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile

from core.paths import UPLOADS_DIR

from ..common import load_json, wb_log
from ..schemas import UploadDeleteReq
from . import body_date, envelope, norm_slot, fail, q_date

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_DOC = {".md", ".txt", ".docx"}
ALLOWED = ALLOWED_IMG | ALLOWED_DOC
TEXT_EXCERPT = 3000
MAX_FILE_BYTES = 10 * 1024 * 1024  # 单文件 10MB（与前端 Materials.tsx 同一规则）

def _next_material_name(updir: Path, manifest: list, suffix: str) -> str:
    """新素材名 = 现有 m 序号最大值 +1（清单与目录取大者）。
    旧算法 len(manifest)+1：删除后再上传会覆盖同名旧文件并产生重复清单项。"""
    import re as _re
    mx = 0
    for e in manifest:
        mm = _re.match(r"^m(\d+)", str(e.get("saved_as") or ""))
        if mm:
            mx = max(mx, int(mm.group(1)))
    for p in updir.glob("m*.*"):
        mm = _re.match(r"^m(\d+)", p.name)
        if mm:
            mx = max(mx, int(mm.group(1)))
    return f"m{mx + 1:02d}{suffix}"


# 加固（P4）：saved_as 只允许服务端生成的命名（m01.png 等），阻断拼接路径删除
_SAVED_AS_RE = re.compile(r"^m\d{2,4}\.(png|jpg|jpeg|webp|md|txt|docx)$")


def _updir(date: str, slot: str) -> Path:
    slot = norm_slot(slot)  # 加固（P4）：阻断 "w01/../.." 式穿越拼接
    d = UPLOADS_DIR / (f"{date}_{slot}" if slot else date)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(date: str, slot: str) -> Path:
    return _updir(date, slot) / "manifest.json"


def _extract_text(p: Path, suffix: str) -> str:
    """抽取文稿类素材的文本（docx 抽段落，md/txt 直读），截断到 TEXT_EXCERPT。"""
    try:
        if suffix == ".docx":
            import docx
            doc = docx.Document(str(p))
            text = "\n".join(par.text for par in doc.paragraphs if par.text.strip())
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
        return re.sub(r"\n{3,}", "\n\n", text).strip()[:TEXT_EXCERPT]
    except Exception as e:  # noqa: BLE001 —— 抽取失败不影响文件保存
        return f"（文本抽取失败：{e}）"


def _load_manifest(date: str, slot: str) -> list:
    return load_json(_manifest_path(date, slot), None) or []


def _save_manifest(date: str, slot: str, items: list) -> None:
    _manifest_path(date, slot).write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


@router.post("/delete")
def delete_upload(req: UploadDeleteReq, request: Request):
    """删除单个素材（文件 + 清单项）。saved_as 经白名单校验，阻断路径穿越。
    路由顺序约定：静态路径 /delete 必须声明在参数路由 /{date} 之前，
    否则会被 /{date}（date="delete"）抢先捕获（同 archive /file 教训）。"""
    date = body_date(request, req.date)
    slot = (req.slot or "").strip()
    saved_as = (req.saved_as or "").strip()
    if not _SAVED_AS_RE.match(saved_as):
        return fail("invalid_name", "非法的素材标识")
    manifest = _load_manifest(date, slot)
    keep, removed = [], None
    for m in manifest:
        if m.get("saved_as") == req.saved_as and removed is None:
            removed = m
            (_updir(date, slot) / req.saved_as).unlink(missing_ok=True)
        else:
            keep.append(m)
    if removed is None:
        return fail("notfound", "素材不存在")
    _save_manifest(date, slot, keep)
    wb_log("upload_delete", date, f"[{slot or '默认'}] 删除素材 {removed.get('name')}")
    return envelope({"deleted": removed, "total": len(keep)})


@router.post("/{date}/{slot}")
@router.post("/{date}")
async def upload_files(request: Request, date: str, slot: str = ""):
    """上传素材（多文件，逐个校验）。图片入素材库并可作生图参考；文稿抽取文本注入写稿。
    /{date} 形态承接空槽位上传（与 GET 列表的两种形态对称，修复尾随空段 404）。"""
    date = body_date(request, date)
    form = await request.form()
    # 注意两点：①同名 'files' 字段须用 multi_items()（items() 去重只剩最后一个）；
    # ②表单里的实例是 starlette.datastructures.UploadFile，fastapi.UploadFile
    #   在 0.138 是另一个类，isinstance 不成立。
    uploads = [v for _k, v in form.multi_items() if isinstance(v, UploadFile)]
    if not uploads:
        return fail("empty", "未收到任何文件")
    updir = _updir(date, slot)
    manifest = _load_manifest(date, slot)

    added, skipped = [], []
    for f in uploads:
        name = Path(f.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED:
            skipped.append({"name": name, "reason": f"不支持的文件类型 {suffix or '（无后缀）'}"})
            continue
        data = await f.read()
        if len(data) == 0:
            skipped.append({"name": name, "reason": "空文件"})
            continue
        if len(data) > MAX_FILE_BYTES:
            skipped.append({"name": name,
                            "reason": f"超过大小限制（{MAX_FILE_BYTES // (1024 * 1024)}MB）"})
            continue
        saved = _next_material_name(updir, manifest, suffix)
        (updir / saved).write_bytes(data)
        entry = {"name": name, "saved_as": saved, "type": "image" if suffix in ALLOWED_IMG else "doc",
                 "size": len(data), "uploaded_at": datetime.now().isoformat(timespec="seconds")}
        if entry["type"] == "doc":
            entry["text"] = _extract_text(updir / saved, suffix)
        manifest.append(entry)
        added.append(entry)
    _save_manifest(date, slot, manifest)
    wb_log("upload", date,
           f"[{slot or '默认'}] 上传素材 {len(added)} 个（跳过 {len(skipped)}）")
    return envelope({"added": added, "skipped": skipped, "total": len(manifest)})


@router.get("/{date}/{slot}")
@router.get("/{date}")
def list_uploads(request: Request, date: str = "", slot: str = ""):
    # date 优先取路径参数（/{date}/{slot} 与 /{date}?slot= 两种形态都支持）
    d = body_date(request, date)
    s = slot or ""
    manifest = _load_manifest(d, s)
    return envelope({"date": d, "slot": s, "materials": manifest,
                     "dir": str(_updir(d, s).relative_to(UPLOADS_DIR.parent))})
