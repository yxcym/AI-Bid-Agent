"""应用装配：所有组件在这里组装一次，路由只取不建。

依赖注入用的是“显式构造”而不是一堆 @Bean / Depends：
项目就六七个组件，一眼能看出谁依赖谁，比框架魔法更好维护。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.common.errors import BizException
from app.config import PROJECT_ROOT, AppConfig, load_config
from app.crawler.registry import build_crawler
from app.docx_gen.generator import WordDocGenerator
from app.intent.base import IntentParser
from app.intent.llm_parser import LlmIntentParser
from app.intent.rule_parser import RuleIntentParser
from app.pipeline.cleaner import RuleCleaner
from app.pipeline.dedup import SqliteFingerprintStore
from app.pipeline.service import PipelineService
from app.push.emailer import EmailPusher
from app.schedule.scheduler import TaskScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bidagent")


class AppContext:
    """运行时容器：一个实例串起全部模块。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.parser: IntentParser = self._build_parser(config)
        self.crawler = build_crawler(config)
        self.cleaner = RuleCleaner(config)
        self.dedup = SqliteFingerprintStore(config.db_path())
        self.docgen = WordDocGenerator(config)
        self.scheduler = TaskScheduler(config)
        self.pusher = EmailPusher(config)
        self.pipeline = PipelineService(
            config=config,
            parser=self.parser,
            crawler=self.crawler,
            cleaner=self.cleaner,
            dedup=self.dedup,
            docgen=self.docgen,
            scheduler=self.scheduler,
            pusher=self.pusher,
        )
        self.scheduler.bind(self._run_scheduled)

    @staticmethod
    def _build_parser(config: AppConfig) -> IntentParser:
        if config.intent.parser == "llm":
            llm = LlmIntentParser()
            if llm.enabled():
                return llm
            log.warning("intent.parser=llm 但未配置 BIDAGENT_LLM_API_KEY，自动回退 rule")
        return RuleIntentParser()

    def _run_scheduled(self, query: str) -> None:
        """定时触发的入口：跑一轮但不再注册新任务（否则自己套自己）。"""
        try:
            self.pipeline.run(query, write_doc=True, allow_schedule=False)
        except Exception:
            log.exception("定时任务执行失败: %s", query)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    ctx = AppContext(config)
    app.state.ctx = ctx
    ctx.scheduler.start()
    log.info(
        "启动完成 | mock=%s | 站点=%d | 指纹=%d | 解析器=%s | 邮件推送=%s",
        config.crawler.mock,
        len(config.enabled_sites()),
        ctx.dedup.count(),
        ctx.parser.name,
        "就绪" if ctx.pusher.enabled else ("未配置" if config.push.email.enabled else "未启用"),
    )
    yield
    ctx.scheduler.shutdown()


app = FastAPI(
    title="AI-Bid-Agent",
    description="招投标信息智能聚合 Agent —— 自然语言进，结构化 Word 出",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(BizException)
async def handle_biz(_: Request, exc: BizException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "data": None},
    )


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    log.exception("未预期异常", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": f"服务内部错误：{type(exc).__name__}", "data": None},
    )


# ---- 前端静态托管：单进程同源形态（演示默认）。部署形态见 docs/前端与部署说明.md ----
_DIST = PROJECT_ROOT / "frontend" / "dist"
if _DIST.exists():
    # API 路由在上面已注册，静态挂载兜底其余路径；html=True 让 / 直出 index.html
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
    log.info("前端已挂载: %s", _DIST)
else:
    log.warning("未找到前端构建产物 %s —— 仅提供 API；请先 cd frontend && npm install && npm run build", _DIST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=load_config().app.port, reload=False)
