"""流水线编排：解析 -> 抓取 -> 清洗 -> 去重 -> 出文档 -> 挂定时。

这个类只做“串流程”，每步的具体实现都在各自模块里，
所以换解析器/抓取器/文档样式，这里一行都不用改。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.common.errors import BizException
from app.config import PROJECT_ROOT, AppConfig, SiteConfig
from app.crawler.base import CrawlerClient, LoginRequiredError
from app.docx_gen.generator import WordDocGenerator
from app.intent.base import IntentParser
from app.models import Notice, PipelineResult, ParsedIntent
from app.pipeline.cleaner import RuleCleaner
from app.pipeline.dedup import SqliteFingerprintStore
from app.schedule.scheduler import TaskScheduler

log = logging.getLogger(__name__)


class PipelineService:
    def __init__(
        self,
        config: AppConfig,
        parser: IntentParser,
        crawler: CrawlerClient,
        cleaner: RuleCleaner,
        dedup: SqliteFingerprintStore,
        docgen: WordDocGenerator,
        scheduler: TaskScheduler,
    ) -> None:
        self._config = config
        self._parser = parser
        self._crawler = crawler
        self._cleaner = cleaner
        self._dedup = dedup
        self._docgen = docgen
        self._scheduler = scheduler

    def parse_only(self, query: str) -> ParsedIntent:
        return self._parser.parse(query, self._config.enabled_sites())

    def run(self, query: str, write_doc: bool = True, allow_schedule: bool = True) -> PipelineResult:
        if not query or not query.strip():
            raise BizException("query 不能为空")

        intent = self.parse_only(query.strip())
        if not intent.keyword or len(intent.keyword) < 2:
            # 单字“关键词”基本是“看/查/帮”这类动词残渣，宁可报错也不拿去全网搜
            raise BizException(
                "没能从这句话里识别出检索关键词。请写成“时间 + 区域 + 主题 + 招标信息”的形式，"
                "例如：最近3个月的上海充电桩招标信息"
            )

        result = PipelineResult(query=query.strip(), intent=intent)
        sites = self._pick_sites(intent)

        all_notices: list[Notice] = []
        for site in sites:
            raw_list = self._safe_fetch(site, intent, result)
            result.fetched += len(raw_list)
            for raw in raw_list:
                notice = self._cleaner.clean(raw)
                if not self._cleaner.is_relevant(notice, intent.keyword):
                    continue
                all_notices.append(notice)

        # ---- 去重：新指纹入库，旧指纹计为重复 ----
        fresh: list[Notice] = []
        for notice in all_notices:
            if self._dedup.is_new(notice.fingerprint):
                notice.is_new = True
                fresh.append(notice)
                self._dedup.save(notice.fingerprint, notice.title, notice.url, notice.source_name)
            else:
                notice.is_new = False

        result.fresh = len(fresh)
        result.deduplicated = len(all_notices) - len(fresh)
        result.items = all_notices

        # ---- 出文档：只写增量，没有增量就不产空文档 ----
        if write_doc:
            if fresh:
                path = self._docgen.generate(intent, fresh)
                result.doc_path = _display_path(path)
            else:
                result.warnings.append("本轮无新增条目，未生成文档（增量推送语义）")

        # ---- 定时：原话带频率才注册 ----
        if allow_schedule and intent.cron:
            result.scheduled_job = self._scheduler.add(result.query, intent.cron)

        log.info(
            "pipeline done query=%s fetched=%d fresh=%d dup=%d doc=%s",
            result.query, result.fetched, result.fresh, result.deduplicated, result.doc_path,
        )
        return result

    # ------------------------------------------------------ 内部

    def _pick_sites(self, intent: ParsedIntent) -> list[SiteConfig]:
        enabled = self._config.enabled_sites()
        if not enabled:
            raise BizException("config.yaml 里没有启用任何站点")
        if not intent.sites:
            return enabled

        picked = [s for s in enabled if s.code in intent.sites]
        if not picked:
            raise BizException(f"指定的站点不存在或未启用：{intent.sites}")
        return picked

    def _safe_fetch(self, site: SiteConfig, intent: ParsedIntent, result: PipelineResult):
        try:
            return self._crawler.fetch(site, intent)
        except LoginRequiredError as exc:
            result.warnings.append(str(exc))
            return []
        except Exception as exc:  # 单站失败不拖垮整轮
            result.warnings.append(f"站点[{site.name}]抓取失败：{type(exc).__name__}: {exc}")
            return []


def _display_path(path: Path) -> str:
    """接口里返回相对项目根的路径，避免把本机绝对路径暴露给前端。"""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name
