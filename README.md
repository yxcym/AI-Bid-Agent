# AI-Bid-Agent | 招投标信息智能聚合 Agent

一个基于自然语言驱动的招投标信息聚合引擎：一句话进，结构化 Word 出。覆盖「意图解析 → 多源抓取 → 清洗去重 → 文档生成 → 定时推送」全链路。

> 2026 AI 先锋未来人才大赛（飞书）参赛项目。真实站点抓取已接入（5 个站点），指纹增量去重、cron 定时与邮件推送齐备；意图解析为 hybrid 模式（规则打底，LLM 补空缺字段，无 key 自动降级）；提供 Docker 双容器、单文件 exe、run.ps1 三种交付形态。

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

外加`app/schedule/`（APScheduler定时）与`app/api/`（FastAPI薄路由层）。设计细节见`docs/详设文档.md`，技术栈对比见`docs/架构选型对比.md`，前端与部署决策见`docs/前端与部署说明.md`，安全边界与门禁见`docs/安全边界与门禁.md`，逐轮进展见`docs/开发日志.md`。

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

- [x] 四模块骨架、规则解析、真实站点抓取、指纹增量、Word生成、cron定时持久化
- [x] Vue 3 前端仪表盘（nginx 托管 / 后端同源托管）
- [x] hybrid 意图解析（规则打底，LLM 补空缺；兼容 DeepSeek/火山方舟/千问/智谱等 OpenAI 兼容 API）
- [x] 邮件推送（SMTP，凭据走 secrets.yaml）
- [x] Docker 双容器门禁部署（secrets 只读挂载、审计脚本）
- [x] PyInstaller 单文件 exe（双击即用，无需 Python/Docker/Node）
- [ ] Playwright登录态侧车（`demo_login`站点占位）
- [ ] 演示模式（禁写接口的只读开关）

明确边界：仅绑 127.0.0.1 回环地址；无鉴权与并发控制，不建议直接暴露公网。

## License

本项目以 Apache License 2.0 开源，详见 [`LICENSE`](LICENSE)。依赖组件均为宽松许可或带分发例外条款。

## Code signing policy

- 当前 Release 附带的 `AI-Bid-Agent.exe` 未做 Authenticode 签名；
- 签名计划：拟通过 SignPath Foundation 开源项目免费签名计划完成签名；
- 发布二进制与 Tag 一一对应，可由 `scripts\build_exe.ps1` 从同一提交复现构建；下载后请核对 Release 页附带的 SHA-256 哈希。
