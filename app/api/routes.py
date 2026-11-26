"""REST 层：薄，不写业务。每个端点 <= 5 行，业务都在 service 里。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.common.errors import BizException

router = APIRouter(prefix="/api", tags=["bid-agent"])


class IntentRequest(BaseModel):
    query: str = Field(..., min_length=2, description="用户原话，如：最近3个月的合肥充电桩招标信息")


class RunRequest(BaseModel):
    query: str = Field(..., min_length=2)
    write_doc: bool = Field(True, description="是否生成 Word")
    schedule: bool = Field(True, description="原话带频率时是否注册定时任务")


def _ctx(request: Request):
    return request.app.state.ctx


# ---------------------------------------------------------------- 基础

@router.get("/health")
def health(request: Request) -> dict:
    ctx = _ctx(request)
    cfg = ctx.config
    data = {
        "status": "ok",
        "mock": cfg.crawler.mock,
        "parser": ctx.parser.name,
        "sites": [
            {"code": s.code, "name": s.name, "enabled": s.enabled, "needs_login": s.needs_login}
            for s in cfg.crawler.sites
        ],
        "fingerprints": ctx.dedup.count(),
        "scheduled_jobs": len(ctx.scheduler.list_jobs()),
        "output_dir": str(cfg.output_path()),
    }
    # 统一信封：前端解包逻辑要求 {success, data}，裸dict会被前端判为失败
    return {"success": True, "data": data}


@router.post("/intent/parse")
def parse_intent(req: IntentRequest, request: Request) -> dict:
    """只解析、不抓取。调规则/提示词时最常用的调试口。"""
    ctx = _ctx(request)
    intent = ctx.pipeline.parse_only(req.query)
    return {"success": True, "data": intent.to_dict()}


# ---------------------------------------------------------------- 主流程

@router.post("/pipeline/run")
def run_pipeline(req: RunRequest, request: Request) -> dict:
    """一句话跑完整条链路：解析 -> 抓取 -> 清洗 -> 去重 -> 出 Word -> 挂定时。"""
    ctx = _ctx(request)
    result = ctx.pipeline.run(req.query, write_doc=req.write_doc, allow_schedule=req.schedule)
    return {"success": True, "data": result.to_dict()}


@router.post("/fingerprints/clear")
def clear_fingerprints(request: Request) -> dict:
    """清空去重库。演示“首次全量 / 二次增量”时用。"""
    ctx = _ctx(request)
    removed = ctx.dedup.clear()
    return {"success": True, "message": f"已清空 {removed} 条指纹", "data": {"removed": removed}}


# ---------------------------------------------------------------- 定时任务

@router.get("/tasks")
def list_tasks(request: Request) -> dict:
    ctx = _ctx(request)
    return {"success": True, "data": ctx.scheduler.list_jobs()}


@router.delete("/tasks/{job_id}")
def delete_task(job_id: str, request: Request) -> dict:
    ctx = _ctx(request)
    if not ctx.scheduler.remove(job_id):
        raise BizException(f"任务不存在：{job_id}", status_code=404)
    return {"success": True, "message": "已删除", "data": {"id": job_id}}


# ---------------------------------------------------------------- 产物

@router.get("/files")
def list_files(request: Request) -> dict:
    ctx = _ctx(request)
    out = ctx.config.output_path()
    if not out.exists():
        return {"success": True, "data": []}
    files = [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "modified": p.stat().st_mtime,
            "url": f"/api/files/{p.name}",
        }
        for p in sorted(out.glob("*.docx"), key=lambda x: x.stat().st_mtime, reverse=True)
    ]
    return {"success": True, "data": files}


@router.get("/files/{name}")
def download_file(name: str, request: Request) -> FileResponse:
    ctx = _ctx(request)
    target = _resolve_inside(ctx.config.output_path(), name)
    if not target.exists():
        raise BizException(f"文件不存在：{name}", status_code=404)
    return FileResponse(str(target), filename=target.name)


def _resolve_inside(base: Path, name: str) -> Path:
    """只允许取 base 目录下的文件名，挡掉 ../ 路径穿越。"""
    if Path(name).name != name:
        raise BizException("非法文件名", status_code=400)
    target = (base / name).resolve()
    if base.resolve() not in target.parents:
        raise BizException("非法路径", status_code=400)
    return target
