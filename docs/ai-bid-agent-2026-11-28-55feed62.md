---
id: "55feed62-606f-4f0e-9a79-6abc2b9add60"
title: "AI-Bid-Agent 代码评审与整改优先级（2026-11-28）"
description: "记录对 AI-Bid-Agent（飞书 AI 未来人才大赛参赛项目）的一轮完整代码评审：做对的判断、P0/P1/P2 问题与改法、对外表述与实现的三处不符、按周/月排的投入优先级，供后续整改或对外说明直接引用。"
status: "active"
created_at: "2026-11-28T11:18:14.578Z"
updated_at: "2026-11-28T11:18:14.578Z"
content_hash: "8e184fd2266cb3c03aa9b04dbf62e0870813e73db46d52e6688406cfa1becc8a"
source_paths:
  - "app/main.py"
  - "app/crawler/http_client.py"
  - "app/pipeline/dedup.py"
  - "app/pipeline/cleaner.py"
  - "app/api/routes.py"
  - "app/docx_gen/generator.py"
  - "app/intent/hybrid_parser.py"
  - "app/push/webhook.py"
  - "app/pipeline/service.py"
  - "deploy/nginx.conf"
  - "deploy/Dockerfile"
  - "deploy/docker-compose.yml"
  - "README.md"
  - "docs/安全边界与门禁.md"
  - "tests/test_webhook.py"
session_ids:
  - "mnemon-run-v1-9b3626a7-4230-42d2-8fc9-d4d1474bb533"
memory_body_ids:
  []
---

# AI-Bid-Agent 代码评审与整改优先级（2026-11-28）

## 背景与总评

对象：`AI-Bid-Agent`（2026 飞书 AI 先锋未来人才大赛参赛项目，仓库根即工作区），评审时规模约 3k 行有效 Python（app 目录 3894 行，非注释约 3042）、约 700 行前端、108 条 pytest 用例、49 个 commit / 5 个 release（2026-11-26 至 11-28）。

总评：工程纪律明显高于同类参赛仓库，已经是一个跑得起来的作品。短板集中在三处——名字跑在实现前面（自称 Agent）、对外描述跑在代码前面、最后 10% 的可用性被交付形态盖住。

## 一、判断正确、值得保留的设计

1. **hybrid 意图解析是整份代码里最成熟的决策**：只在两种情况下升级到模型——关键词缺失或为垃圾词、原话疑似带频率但规则没解出 cron；日期与站点始终以规则为准，模型失败整条回退（`app/intent/hybrid_parser.py`）。「让绝大多数请求不产生模型调用」是产品级成本判断。
2. **摘要的结构性防幻觉**：只让模型输出编号，标题、链接、来源由代码按编号回填，模型没有「编一条公告」的位置（`app/summary/summarizer.py`、`app/docx_gen/generator.py:109-122`）。
3. **飞书/钉钉机器人加签差异被写进注释并用测试锁住**：飞书 `key={ts}\n{secret}` 与钉钉 `key=secret` 的互换、query 覆盖、双重编码（`app/push/webhook.py`，回归在 `tests/test_webhook.py`，276 行）。
4. **增量语义从抓取贯穿到推送**：无新增则不出文档、不发推送（`app/pipeline/service.py:102-123`），并把中性提示 `notes` 与告警 `warnings` 分成两级。
5. **清洗与去重的关键细节到位**：`fingerprint(title, url)` 先去掉查询参数与锚点再哈希，否则 utm/时间戳会让同一篇文章每次都算「新」（`app/pipeline/cleaner.py:94-103`）；详情页正文按句保留换行，Word 里是分段而非一整块。
6. **安全上有少见的自觉**：`/api/files/{name}` 用 `_resolve_inside` 挡 `../` 穿越（`app/api/routes.py:201-208`）；凭据走 gitignore、审计脚本带 `.bak` 漂移告警；`demo_mode` 让评委点不坏。安全文档自认「房间的锁从来没装过，只是门开在荒岛上」（`docs/安全边界与门禁.md:70`），这种诚实比「全部完成」更可信。

## 二、问题清单（按严重度）

### P0-1 CORS 通配叠加零鉴权
`app/main.py:135-140` 为 `allow_origins=["*"]`、`allow_methods=["*"]`。前后端本就同源（nginx 把 `/api` 反代到 8000），这段中间件无必要；实际打开的是任意网页可对 `127.0.0.1` 跨域读写——列文档、触发全链路、清指纹、删定时任务、发测试邮件。Chrome 的 Private Network Access 可能拦一部分，但把安全押在浏览器默认值上正是安全文档反对的做法。
改法：删掉该中间件，或白名单到实际前端 origin。

### P0-2 抓取失败被吞，空结果与「没有新标」不可区分
`app/crawler/http_client.py:49-54` 是 `except httpx.HTTPError: break`，无日志、无返回信号。站点 403/超时/Cookie 失效时 `_safe_fetch` 看到的是正常空列表，用户读到「本轮 0 条新增」，以为今天没标——与该项目的增量提示设计正好相反，是最伤信任的一类静默失败。
改法：让 `fetch` 返回每站状态，pipeline 记 `warnings`，前端把「该站失败」与「0 条新增」用两种颜色区分。

### P0-3 指纹去重的并发与生命周期
`app/pipeline/dedup.py:31-42`：`is_new` 查一次、`save` 再写一次，两次独立连接；并发 run（定时 + 手动）可同时判 new，重复出文档、重复推送。`with sqlite3.connect(...)` 只提交不关闭，长跑进程会攒句柄。指纹还是全局的：两个不同查询命中同一条公告，后一个查询永远看不到它。
改法：靠 `INSERT OR IGNORE` 的 rowcount 判 new；`contextlib.closing` 管连接；指纹按查询/订阅维度建作用域，保留全局语义则须写进 README。

### P1-1 长任务同步跑在请求里，对不上 nginx 超时
`app/api/routes.py:88-97` 为同步函数，`deploy/nginx.conf:38` 是 `proxy_read_timeout 120s`。3 站 2 页 + 每站 12 条详情（ccgp 还走 Playwright）超过 2 分钟完全可能；超时后 nginx 给 504，后端继续跑完继续推送，用户再点一次就叠加一轮。
改法：后台任务 + `/api/jobs/{id}` 轮询；最低成本是进程内锁，重入返回 409，并把超时提到 300s。

### P1-2 Word 成色撑不住「正式交付文档」
抽查 `output/` 内 48KB 文档：原文链接是纯文本（`app/docx_gen/generator.py:152-154` 只上颜色，未建 relationship，全文 `w:hyperlink` 计数为 0）；所有段落都是 Normal 样式、无 Heading，导航窗格与 TOC 全空；正文残留网页排版的大段空格与断行。文件名 `{查询}_{YYYYmmddHHMM}.docx`（`generator.py:47`）同一分钟两轮会互相覆盖。均为一小时级别的活，却是「像不像产品」的分界线。

### P1-3 容器形态
`deploy/Dockerfile` 无 `USER`（root 运行）；`deploy/docker-compose.yml` 无 healthcheck 与资源限制；为 ccgp 把 Chromium 烤进主镜像，体积 2.08GB、离线包 590MB。建议拆 slim（无浏览器，与 exe 同款能力）与 full 两个 tag。

### P2-1 没有 CI
仓库无 `.github/`；exe 与 590MB 镜像均为手工构建，SHA-256 手贴进 Release 正文，README 的「可由同一提交复现构建」无流水线佐证；SignPath 免费签名也要求有 CI。一个 `pytest` + build + `sha256sum` 的 workflow 即可。

### P2-2 前端是 517 行单文件 + 841 行 CSS
无组件拆分、无 `vue-tsc --noEmit`、无 lint、无前端测试，`package.json` 连 typecheck 脚本都没有。页面本身不差（ECharts 按需注册、错误态分明），属没有框架约束的单页写法。

### P2-3 文档比例失衡
`docs` 约 189KB 对 3k 行有效代码，其中开发日志 101KB、38 轮，安全文档在教读者什么是回环地址。讲义式内容对学习记录有价值、对外部读者是噪音：建议教学段落移到 `docs/notes/`，README 一屏讲完，其余按读者分流。

## 三、实跑才看得见的问题：双实例

评审时该机器上 Docker(:80) 与本地 uvicorn(:8000) 同时在跑，`/api/health` 都返回 ok，但各自一份指纹库（163 对 22）、`output_dir` 不同（`/srv/bidagent/output` 对 Windows 本地路径）。这正是 README:21 自己警告的「两条路径共用 db、不可同时运行」；当时没出事只因为两边 `scheduled_jobs` 都是 0。
改法：启动时检测另一实例并拒启，或把 db 路径写进 health，让人一眼看出在看哪一套。

## 四、对外表述三处与实现不符

1. GitHub About 写「Java + Python」与「支持登录态」：仓库里没有 Java；`demo_login` 是 `example-bid-portal.invalid` 占位，README 自己都没打勾（`README.md:67`，并注明「至少1个登录站点」出自方案稿自设指标而非赛题硬性要求）。建议改为「Python + Vue 的招投标信息聚合工具（意图解析、多源抓取、去重、Word、定时推送）」，登录态与混合栈列为后续方向。
2. `README.md:5` 写「真实站点抓取已接入（5 个站点）」：配置 5 条、`enabled` 3 条（hefei_gc、hefei_cg、ccgp），其中合肥工程与合肥政采是同一平台两个栏目，独立来源 2 个；exe 形态无 Chromium 会跳过 ccgp，实际只剩 1 个平台。建议表述为「3 个可用站点（2 个独立来源），exe 形态 1 个」。
3. README 无界面截图，带 ECharts 仪表盘的项目第一眼看不到界面，损失判分与转发。

## 五、投入优先级

- **一周内（廉价高回报）**：删 CORS；抓取失败可见化；Word 加超链接与 Heading；一条 CI（pytest + 出包 + 校验和）。
- **一个月**：长任务异步化 + 并发锁；指纹语义收紧与清理；每日站点探针，失败进 health；再补 2 个真独立来源；飞书侧从 webhook 升级到交互卡片或多维表格。
- **不建议做**：为「Agent」而加规划循环（规则打底 + 模型补位比多数套壳 Agent 稳）；把 Java 混栈搬进来，除非它本身就是学习目标（那应单开仓库）。

## 一句话结论

代码比名字硬：按现有实现它是一条扎实的自动化流水线，「AI」只出现在两个受约束的小任务上——这在工程上是对的，在比赛叙事上要补。真正该补的是把对外说的与代码里有的对齐，再补上那 10% 可用性：链接可点、失败可见、超时可控、实例唯一。
