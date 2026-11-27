"""定时任务：APScheduler（BackgroundScheduler）+ SQLite 持久化。

设计：
  - 注册入口只有 add() 一个：内存注册（APScheduler）＋落盘（SqliteJobStore）一步完成
  - restore() 在进程启动后逐条重放落盘任务——这就是"重启不丢任务"的全部实现
  - 删除时内存与落盘同步删，两边永远一致

为什么不用 APScheduler 的 SQLAlchemyJobStore：见 store.py 模块注释。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.common.errors import BizException
from app.config import AppConfig
from app.schedule.store import SqliteJobStore

log = logging.getLogger(__name__)

# 标准 cron 周日=0，APScheduler 用 mon..sun 名称，这里显式映射，避免歧义
_DOW = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


class TaskScheduler:
    def __init__(self, config: AppConfig, store: SqliteJobStore | None = None) -> None:
        self._config = config
        self._store = store
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
        self._register(job_id, query, cron)
        if self._store is not None:
            self._store.save(job_id, query, cron)
        return job_id

    def restore(self) -> int:
        """进程启动后重放落盘任务。返回恢复条数；单条失败只记日志，不拖垮其余。"""
        if not self._config.schedule.enabled or self._store is None or self._job_func is None:
            return 0
        restored = 0
        for job_id, query, cron in self._store.all():
            try:
                self._register(job_id, query, cron)
                restored += 1
            except Exception:
                log.exception("定时任务恢复失败 job=%s query=%s", job_id, query)
        return restored

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
        in_memory = self._scheduler.get_job(job_id) is not None
        if in_memory:
            self._scheduler.remove_job(job_id)
        in_store = self._store.delete(job_id) if self._store is not None else False
        return in_memory or in_store

    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------ 内部

    def _register(self, job_id: str, query: str, cron: str) -> None:
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
