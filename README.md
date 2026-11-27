# AI-Bid-Agent | 招投标信息智能聚合 Agent

一个基于自然语言驱动的招投标信息聚合引擎：一句话进，结构化 Word 出。覆盖「意图解析 → 多源抓取 → 清洗去重 → 文档生成 → 定时推送」全链路。

> 2026 AI 先锋未来人才大赛（飞书）参赛项目。当前为v1骨架版：mock 数据可完整演示全链路，真实站点抓取只需校准选择器后把`crawler.mock`改为`false`。

## 快速开始（Docker，当前默认形态）

```powershell
# 1. 首次：构建前端（node 18+，产物在 frontend/dist）
cd frontend; npm install; npm run build; cd ..

# 2. 起两个容器：nginx(80) 托管前端并把 /api/ 反代到 app(内网8000)
docker compose -f deploy/docker-compose.yml up -d --build

# 3. 浏览器打开 http://127.0.0.1/ —— 点示例短语、点「执行全链路」、看统计与文档
```

Docker Desktop 已设为登录自启，容器策略`restart: unless-stopped`——重开电脑后无需任何手动操作，等引擎起来约十几秒即可访问。停止：`docker compose -f deploy/docker-compose.yml down`（不动`data/`、`output/`）。

**无 Docker 的备选路径**：`.\run.ps1` 单进程直出，前端由 FastAPI 同源托管，访问 `http://127.0.0.1:8000/`。**两条路径共用`data/bidagent.db`，不可同时运行**（会出现两套调度器重复推送）。

同一句话立即再执行 → 新增 0、不产新文档：这就是增量推送语义。前端形态与 nginx/docker 取舍见`docs/前端与部署说明.md`。

## 一句话系统说明

用户输入自然语言（如上例），系统拆出**关键词＝充电桩、区域＝上海、时间＝最近3个月、频率＝每天9:00**；有频率就注册定时任务，没有就立即执行。抓取结果经规则清洗剥广告、SQLite指纹去重后，只把**新增**条目按`{用户的问题}_{时间}.docx`命名生成结构化Word。

## 四模块架构

| 模块 | 目录 | 职责 | 关键决策 |
|---|---|---|---|
| 意图解析 | `app/intent/` | 原话 → 结构化意图 | 规则解析打底，LLM解析器预留且失败自动回退 |
| 多源采集 | `app/crawler/` | 站点列表页 → RawNotice | HTTP/Jsoup先行，登录态站点明确报错不静默失败 |
| 清洗去重 | `app/pipeline/` | 剥广告、截断、指纹增量 | 标题＋归一化URL做MD5指纹 |
| 文档生成 | `app/docx_gen/` | 新增条目 → Word | 中文宋体、英文Times New Roman分开设 |
| 前端 | `frontend/` | Vue3＋Vite＋TS单页仪表盘 | 构建产物由 nginx 容器托管（`/api/` 反代到 app）；无 Docker 时退回后端同源托管 |

外加`app/schedule/`（APScheduler定时）与`app/api/`（FastAPI薄路由层）。设计细节见`docs/详设文档.md`，技术栈对比见`docs/架构选型对比.md`，前端与部署决策见`docs/前端与部署说明.md`，逐轮进展见`docs/开发日志.md`。

## 接口一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 运行状态、站点清单、指纹数 |
| POST | `/api/intent/parse` | 只解析不抓取，调解析规则用 |
| POST | `/api/pipeline/run` | 全链路：解析→抓取→清洗→去重→出Word→挂定时 |
| POST | `/api/fingerprints/clear` | 清空指纹库（演示“首次全量/二次增量”） |
| GET/DELETE | `/api/tasks`、`/api/tasks/{id}` | 查看/删除定时任务 |
| GET | `/api/files`、`/api/files/{name}` | 列出/下载生成的Word |

## 当前进度与边界

- [x] 四模块骨架、规则解析、mock全链路、指纹增量、Word生成、cron定时
- [x] Vue 3 前端仪表盘（构建产物由后端同源托管）
- [ ] 真实站点选择器校准（抓包后改`config/config.yaml`）
- [ ] Playwright登录态侧车（`demo_login`站点占位）
- [ ] LLM清洗与解析接入（`intent.parser: llm`＋环境变量即可启用）

明确边界：定时任务不持久化（进程重启丢失）；无鉴权与并发控制，仅本机演示用。
