"""REST 层：薄，不写业务。每个端点 <= 5 行，业务都在 service 里。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import __version__ as APP_VERSION
from app.common.errors import BizException
from app.push.emailer import EmailPushError
from app.push.webhook import WebhookPushError

router = APIRouter(prefix="/api", tags=["bid-agent"])


class IntentRequest(BaseModel):
    query: str = Field(..., min_length=2, description="用户原话，如：最近3个月的合肥充电桩招标信息")


class RunRequest(BaseModel):
    query: str = Field(..., min_length=2)
    write_doc: bool = Field(True, description="是否生成 Word")
    schedule: bool = Field(True, description="原话带频率时是否注册定时任务")


def _ctx(request: Request):
    return request.app.state.ctx


def _forbid_demo(request: Request, action: str) -> None:
    """演示模式守卫：看得见数据，点不出副作用。"""
    if _ctx(request).config.app.demo_mode:
        raise BizException(f"演示模式已开启，「{action}」已禁用（config.yaml 的 app.demo_mode）", status_code=403)


# ---------------------------------------------------------------- 基础

@router.get("/health")
def health(request: Request) -> dict:
    ctx = _ctx(request)
    cfg = ctx.config
    data = {
        "status": "ok",
        "version": APP_VERSION,
        "mock": cfg.crawler.mock,
        "demo_mode": cfg.app.demo_mode,
        "parser": ctx.parser.name,
        "sites": [
            {"code": s.code, "name": s.name, "enabled": s.enabled, "needs_login": s.needs_login}
            for s in cfg.crawler.sites
        ],
        "fingerprints": ctx.dedup.count(),
        "scheduled_jobs": len(ctx.scheduler.list_jobs()),
        "output_dir": str(cfg.output_path()),
        "push": ctx.pusher.status(),
        "push_webhook": ctx.webhook.status(),
        "summary": {"enabled": ctx.summarizer.enabled(), "model": ctx.summarizer.model if ctx.summarizer.enabled() else None},
        "run_history": ctx.history.count(),
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
    demo = ctx.config.app.demo_mode
    allow_schedule = req.schedule and not demo
    result = ctx.pipeline.run(req.query, write_doc=req.write_doc, allow_schedule=allow_schedule)
    if demo and req.schedule and not result.scheduled_job:
        result.warnings.append("演示模式：定时注册已跳过（写操作禁用）")
    return {"success": True, "data": result.to_dict()}


@router.post("/fingerprints/clear")
def clear_fingerprints(request: Request) -> dict:
    """清空去重库。演示“首次全量 / 二次增量”时用。"""
    _forbid_demo(request, "清空指纹库")
    ctx = _ctx(request)
    removed = ctx.dedup.clear()
    return {"success": True, "message": f"已清空 {removed} 条指纹", "data": {"removed": removed}}


# ---------------------------------------------------------------- 历史趋势

@router.get("/history/daily")
def history_daily(request: Request, days: int = 14) -> dict:
    """按日聚合的运行/新增统计（缺数日期补零），前端画趋势图。"""
    ctx = _ctx(request)
    return {"success": True, "data": ctx.history.daily(days=days)}


@router.get("/history/recent")
def history_recent(request: Request, limit: int = 20) -> dict:
    """最近几轮流水线的原始记录。"""
    ctx = _ctx(request)
    return {"success": True, "data": ctx.history.recent(limit=limit)}


# ---------------------------------------------------------------- 推送

@router.post("/push/test")
def test_push(request: Request) -> dict:
    """给所有已启用渠道各发一条连通性测试（邮件 + 飞书/钉钉群），不用等真实抓取。"""
    _forbid_demo(request, "推送自测")
    ctx = _ctx(request)
    sent: list[str] = []
    errors: list[str] = []
    if ctx.pusher.enabled:
        try:
            sent.append(f"邮件→{ctx.pusher.send_test()}")
        except EmailPushError as exc:
            errors.append(str(exc))
    if ctx.webhook.enabled:
        try:
            sent.append(f"群机器人→{ctx.webhook.send_test()}")
        except WebhookPushError as exc:
            errors.append(str(exc))
    if not sent and not errors:
        raise BizException("没有任何已启用的推送渠道：先在 config/secrets.yaml 填邮件凭据或 webhook 地址")
    if errors and not sent:
        raise BizException("；".join(errors))
    return {
        "success": True,
        "message": "已发送：" + "、".join(sent) + ("；失败：" + "；".join(errors) if errors else ""),
        "data": {"sent": sent, "errors": errors},
    }


# ---------------------------------------------------------------- 定时任务

@router.get("/tasks")
def list_tasks(request: Request) -> dict:
    ctx = _ctx(request)
    return {"success": True, "data": ctx.scheduler.list_jobs()}


@router.delete("/tasks/{job_id}")
def delete_task(job_id: str, request: Request) -> dict:
    _forbid_demo(request, "删除定时任务")
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
