"""定时任务：APScheduler（BackgroundScheduler）。

为什么不用 Quartz 那套：这里只有一个进程、任务就是“再跑一次流水线”，
上数据库持久化 JobStore 属于给竞赛演示加包袱。进程重启后任务消失，
这一点在操作指南里写明，生产化再换持久化。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.common.errors import BizException
from app.config import AppConfig

# 标准 cron 周日=0，APScheduler 用 mon..sun 名称，这里显式映射，避免歧义
_DOW = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


class TaskScheduler:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._scheduler = BackgroundScheduler(timezone=config.schedule.timezone)
        self._job_func: Callable[[str], None] | None = None

    def bind(self, job_func: Callable[[str], None]) -> None:
        self._job_func = job_func

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def add(self, query: str, cron: str) -> str:
        if not self._config.schedule.enabled:
            raise BizException("定时功能已在配置中关闭（schedule.enabled=false）")
        if self._job_func is None:
            raise BizException("调度器尚未绑定执行器")

        job_id = "bid-" + hashlib.md5(f"{query}|{cron}".encode()).hexdigest()[:10]
        self._scheduler.add_job(
            self._job_func,
            trigger=to_trigger(cron, self._config.schedule.timezone),
            args=[query],
            id=job_id,
            name=query,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        return job_id

    def list_jobs(self) -> list[dict]:
        return [
            {
                "id": job.id,
                "query": job.args[0] if job.args else "",
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]

    def remove(self, job_id: str) -> bool:
        if self._scheduler.get_job(job_id) is None:
            return False
        self._scheduler.remove_job(job_id)
        return True

    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")


def to_trigger(cron: str, timezone: str) -> CronTrigger:
    fields = cron.split()
    if len(fields) != 5:
        raise BizException(f"cron 必须是五段（分 时 日 月 周），收到：{cron}")
    minute, hour, day, month, dow = fields
    if dow not in ("*", "?"):
        dow = _DOW.get(dow, dow)
    return CronTrigger(
        minute=minute, hour=hour, day=day, month=month, day_of_week=dow, timezone=timezone
    )
