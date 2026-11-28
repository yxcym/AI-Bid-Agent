---
id: "aef607d6-5c9b-4007-a0ee-3b54a4ad3c75"
title: "AI-Bid-Agent 整改范围裁决：四条建议撤回与最终清单（2026-11-28）"
description: "记录用户对 AI-Bid-Agent 评审建议的裁决：指纹查询作用域、slim/full 双 tag、后台 job 队列、前端组件拆分四条因\"针对不存在的需求\"被撤回及各自理由；唯一保留项（并发锁挂在 PipelineService.run 而非路由层）；最终 v1.3.2 / v1.4.0 / 工程化三档清单。用于避免后续重复提出已被否决的改造，并修正文档 55feed62 中与之冲突的改法建议。"
status: "active"
created_at: "2026-11-28T11:29:04.426Z"
updated_at: "2026-11-28T11:29:04.426Z"
content_hash: "6180ad68315fe4ece215d637fd4b049023b86d8dc67a376b783493fb0cca73da"
source_paths:
  - "app/main.py"
  - "app/api/routes.py"
  - "app/pipeline/dedup.py"
  - "app/pipeline/service.py"
  - "deploy/nginx.conf"
  - "frontend/src/App.vue"
  - "frontend/src/style.css"
session_ids:
  - "mnemon-run-v1-3a97d25c-06d2-4bbf-97e2-84cbd0aa74e5"
memory_body_ids:
  []
---

# AI-Bid-Agent 整改范围裁决（2026-11-28）

## 背景

同日先后产出两份评审记录：后端/安全/交付层见文档 `55feed62-606f-4f0e-9a79-6abc2b9add60`，前端界面见文档 `572b814d-8780-4c67-b612-9bc3468fcc3e`。用户随后逐条驳回其中四条建议，理由是「给不存在的需求提方案」，并给出记账式证据。本文记录这次范围裁决与最终清单；**本文的裁决优先于那两份文档中对应的改法建议**（55feed62 的 P0-3「指纹按查询作用域」、P1-1「后台 job 队列 + 轮询」不再执行；572b814d 的方案 B「拆 11 个组件」不再执行）。

评审者的共同毛病被点名：把套模板的通用建议混进基于本仓库证据的结论里。

## 一、撤回的四条（含撤回理由）

1. **指纹按查询作用域 —— 归类错误，不是 bug 是产品语义。**
   「见过就不推第二遍」是单用户工具的免打扰设计；改成按关键词隔离后，同一条公告会在每个查询里各推一次。运行历史可验证全局语义在工作：`fresh` 17→0→0、去重 4→21→21 的连续三轮。
   处理：保留全局语义，只在 README 写明"指纹是全局的"；真有第二个使用者再议。
2. **slim / full 双 Docker tag —— 抓错了成本。**
   真实成本是 618MB 离线包重传，属发布流程问题；拆镜像没有消费者，exe（61.7MB）已经是轻量形态。
3. **后台 job 队列 + 轮询 —— 过度设计，且原判断缺实测支撑。**
   「3 站 2 页 + 详情加 Playwright 完全可能超 120 秒」是推断而非实测；历史实际运行间隔 11~81 秒。单机单用户下，进程锁 + 重入 409 + `deploy/nginx.conf` 的 `proxy_read_timeout` 提到 300s 已覆盖全部场景。
4. **前端拆 11 个组件 —— 套模板。**
   517 行未到需要拆的量级；拆完「页面上哪儿都不贴」的问题仍在，根因在 CSS token 与层级，不在文件长度。前端只补 `vue-tsc --noEmit` 类型检查。

## 二、唯一保留并改实施位置的一条

**并发锁：挂在 `PipelineService.run`，不是路由层。**
理由：定时路径 `_run_scheduled`（`app/main.py:97-102`）与 API 路由都直接调用 `PipelineService.run`，只在 `app/api/routes.py` 加锁挡不住「定时任务 + 手动点一次」并发，而这正是 55feed62 记录的 P0-3 并发场景。配合 nginx 超时提到 300s，替代原「后台队列」方案。

## 三、最终清单（按版本归位）

### v1.3.2

- 删 `app/main.py` 中的 CORS 通配中间件（`allow_origins=["*"]` + `allow_methods=["*"]`，前后端同源，无必要）。
- 抓取吞错改为抛出/上报：`app/crawler/http_client.py` 的 `except httpx.HTTPError: break` 让站点失败与「0 条新增」不可区分，是最伤信任的静默失败。
- `app/pipeline/dedup.py`：用 `INSERT OR IGNORE` 的 rowcount 判新，替代 `is_new` 查 + `save` 写的双连接竞态；连接用 `contextlib.closing` 关掉。
- README 补一句全局去重语义。
- health 暴露 `data_dir`：本机曾出现 Docker(:80) 与本地 uvicorn(:8000) 同时在跑、各自一份指纹库（163 对 22）的情况，应在 health 里一眼看出在看哪套数据。

### v1.4.0

- `PipelineService` 层并发锁 + nginx 300s。
- Word 三件：原文链接建真 hyperlink relationship（现为纯文本着色）、段落上 Heading（导航窗格与 TOC 全空）、文件名改用带秒或 uuid 避免同分钟覆盖。
- 容器非 root 用户 + healthcheck。
- README 三处纠偏（5 站点 ↔ 实际 3 enabled / 2 个独立来源；Java+Python 表述；登录态状态）+ 补界面截图。

### 工程化

- 一条 CI：pytest + 出包 + sha256 校验（现无 `.github/`，「可由同一提交复现构建」无流水线证明）。
- `vue-tsc` 类型检查。

## 四、可复用的判断

- 建议先分「产品语义 / 缺陷 / 工程化」三类再排序；把产品语义写进 bug 清单会让改动破坏原有免打扰设计。
- 每条建议都要能回答"谁在为它付费、它替代了什么"，否则就是给不存在的需求提方案。
- 并发类修复要先确认所有调用入口（此处是定时调度与 API 双入口），锁的位置比锁的存在更关键。
