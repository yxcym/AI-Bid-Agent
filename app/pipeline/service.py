"""流水线编排：解析 -> 抓取 -> 清洗 -> 去重 -> 出文档 -> 挂定时。

这个类只做“串流程”，每步的具体实现都在各自模块里，
所以换解析器/抓取器/文档样式，这里一行都不用改。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.common.errors import BizException
from app.config import BASE_DIR, AppConfig, SiteConfig
from app.crawler.base import CrawlerClient, CrawlError, LoginRequiredError, PlaywrightUnavailableError
from app.docx_gen.generator import WordDocGenerator
from app.intent.base import IntentParser
from app.models import Notice, ParsedIntent, PipelineResult, RawNotice
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
        pusher: Any | None = None,
        pushers: list[Any] | None = None,
        summarizer: Any | None = None,
        history: Any | None = None,
    ) -> None:
        self._config = config
        self._parser = parser
        self._crawler = crawler
        self._cleaner = cleaner
        self._dedup = dedup
        self._docgen = docgen
        self._scheduler = scheduler
        # 推送器走鸭子类型（.enabled / .send_digest / .label），换短信/webhook 渠道不用改这里。
        # 多通道并行推送：任一渠道失败只记 warning，不拖累其它渠道与主链路。
        self._pushers: list[Any] = [p for p in (pusher, *(pushers or [])) if p is not None]
        # 摘要器同样鸭子类型（.enabled / .summarize），失败进 warnings 不阻断
        self._summarizer = summarizer
        # 运行历史（.record），失败只记日志不阻断主链路
        self._history = history

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
        failed_sites: list[str] = []  # 没抓到的站点：决定「0 新增」这句话该怎么说
        for site in sites:
            raw_list, failed = self._safe_fetch(site, intent, result)
            if failed:
                failed_sites.append(site.name)
            result.fetched += len(raw_list)
            for raw in raw_list:
                notice = self._cleaner.clean(raw)
                if not self._cleaner.is_relevant(notice, intent.keyword):
                    continue
                all_notices.append(notice)

        # ---- 去重：新指纹入库，旧指纹计为重复 ----
        fresh: list[Notice] = []
        for notice in all_notices:
            # 判新与入库是同一条 INSERT OR IGNORE：并发 run 不会同时判新而重复出文档、重复推送
            notice.is_new = self._dedup.add(notice.fingerprint, notice.title, notice.url, notice.source_name)
            if notice.is_new:
                fresh.append(notice)

        result.fresh = len(fresh)
        result.deduplicated = len(all_notices) - len(fresh)
        result.items = all_notices

        # ---- 正文补抓：只对新增条目、只在本轮真出文档时做（没有落点就不白花时间）----
        if write_doc and fresh:
            self._enrich_details(fresh, sites, result)

        # ---- 出文档：只写增量，没有增量就不产空文档 ----
        if write_doc:
            if fresh:
                # 摘要只在有文档时才做：没有文档就没有落点，白烧 token
                summary = self._summarize(intent, fresh, result)
                path = self._docgen.generate(intent, fresh, summary)
                result.doc_path = _display_path(path)
                # ---- 推送：文档生成了就送达（无文档即无新增，天然免打扰） ----
                for pusher in self._pushers:
                    if not pusher.enabled:
                        continue
                    try:
                        result.pushed_to.append(pusher.send_digest(intent, fresh, path, summary))
                    except Exception as exc:
                        channel = getattr(pusher, "label", "推送")
                        result.warnings.append(f"{channel}推送失败：{exc}")
            elif failed_sites:
                # 有站点没抓到时，「0 新增」不等于「今天没有新公告」，必须把话说全
                result.warnings.append(
                    f"本轮 0 新增，但 {len(failed_sites)} 个站点没抓到（{'、'.join(failed_sites)}），"
                    "结果不完整，不能据此判断今天没有新公告"
                )
            else:
                result.warnings.append("本轮无新增条目，未生成文档（增量推送语义）")

        # ---- 定时：原话带频率才注册 ----
        if allow_schedule and intent.cron:
            result.scheduled_job = self._scheduler.add(result.query, intent.cron)

        log.info(
            "pipeline done query=%s fetched=%d fresh=%d dup=%d doc=%s",
            result.query, result.fetched, result.fresh, result.deduplicated, result.doc_path,
        )
        self._record_history(result)
        return result

    def _record_history(self, result: PipelineResult) -> None:
        if self._history is None:
            return
        try:
            self._history.record(
                query=result.query,
                keyword=result.intent.keyword,
                region=result.intent.region,
                fetched=result.fetched,
                fresh=result.fresh,
                deduplicated=result.deduplicated,
                has_doc=result.doc_path is not None,
                parser=result.intent.parser,
                warnings=len(result.warnings),
            )
        except Exception:
            log.exception("运行历史记录失败（不影响主链路）")

    # ------------------------------------------------------ 内部

    def _summarize(self, intent: ParsedIntent, fresh: list[Notice], result: PipelineResult) -> dict | None:
        """摘要失败不阻断：模型/网络问题只进 warnings，文档照常出。"""
        if self._summarizer is None or not self._summarizer.enabled:
            return None
        try:
            summary = self._summarizer.summarize(intent, fresh)
        except Exception as exc:
            result.warnings.append(f"摘要未生成：{exc}")
            return None
        result.summary = summary
        return summary

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

    def _enrich_details(self, fresh: list[Notice], sites: list[SiteConfig], result: PipelineResult) -> None:
        """对新增条目补抓详情页正文：按站点分组、每站限量、失败只记一条汇总。"""
        fetch_details = getattr(self._crawler, "fetch_details", None)
        if fetch_details is None or not self._config.crawler.detail_enabled or not fresh:
            return

        cfg = self._config.crawler
        site_map = {s.code: s for s in sites}
        groups: dict[str, list[Notice]] = {}
        for notice in fresh:
            groups.setdefault(notice.source_code, []).append(notice)

        attempted = 0
        done = 0
        for code, group in groups.items():
            site = site_map.get(code)
            if site is None:
                continue
            cap = min(len(group), cfg.detail_max_per_site)
            attempted += cap
            try:
                urls = fetch_details(site, group, cap)
            except PlaywrightUnavailableError as exc:  # 轻量版不含浏览器：提示而非告警
                attempted -= cap  # 整站跳过不计入补全率，否则还会多一条「补全 0/N」告警
                result.notes.append(exc.short)
                continue
            except Exception as exc:  # 单站详情失败不影响主链路
                result.warnings.append(f"站点[{site.name}]详情页抓取失败：{type(exc).__name__}: {exc}")
                continue
            if not urls:
                continue
            hit = set(urls)
            for notice in group:
                if notice.url in hit:
                    # 正文同样过去广告与统一截断，保证文档与摘要口径一致
                    notice.content = self._cleaner.clean_block(notice.content)
            done += len(urls)

        result.detail_fetched = done
        missed = attempted - done
        if attempted and missed:
            result.warnings.append(f"详情页正文补全 {done}/{attempted} 条，其余沿用列表页摘要")

    def _safe_fetch(
        self, site: SiteConfig, intent: ParsedIntent, result: PipelineResult
    ) -> tuple[list[RawNotice], bool]:
        """抓单站，返回 (条目, 是否失败)。失败不拖垮整轮，但调用方必须知道这一站没抓到。"""
        try:
            return self._crawler.fetch(site, intent), False
        except LoginRequiredError as exc:
            result.warnings.append(str(exc))
            return [], True
        except PlaywrightUnavailableError as exc:  # 形态差异不等于故障，不计入失败站点
            result.notes.append(exc.short)
            return [], False
        except CrawlError as exc:  # 消息本身已是面向用户的完整句子（含站点名与原因）
            partial = list(exc.partial)
            if partial:
                result.warnings.append(f"{exc}——已抓到的 {len(partial)} 条仍计入本轮，其余缺失")
            else:
                result.warnings.append(f"{exc}——该站本轮没有抓到任何条目")
            return partial, True
        except Exception as exc:  # 单站失败不拖垮整轮
            result.warnings.append(f"站点[{site.name}]抓取失败：{type(exc).__name__}: {exc}")
            return [], True


def _display_path(path: Path) -> str:
    """接口里返回相对运行根目录的路径，避免把本机绝对路径暴露给前端。"""
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return path.name
