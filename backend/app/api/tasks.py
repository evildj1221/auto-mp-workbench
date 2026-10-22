#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/app/api/tasks.py · 后台任务状态（原 /api/task/<tid> 的 JSON 化 + 任务列表）。"""
from fastapi import APIRouter

from .. import taskrunner
from . import envelope, fail

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def task_status(tid: str) -> dict | None:
    t = taskrunner.get_task(tid)
    if not t:
        return None
    return {"status": t["status"], "output": t["output"][-4000:],
            "label": t["label"], "finished_at": t["finished_at"]}


def task_list(limit=5):
    return taskrunner.list_tasks(limit)


@router.get("/{tid}")
def get_task(tid: str):
    t = task_status(tid)
    if not t:
        return fail("missing")
    return envelope(t)


@router.get("")
@router.get("/")
def list_tasks(limit: int = 5):
    return envelope({"tasks": task_list(limit)})
