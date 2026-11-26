
# 🤖 AI-Bid-Agent | 招投标信息智能聚合 Agent

> **中文** | [English](#english-section)

An intelligent bidding information aggregation engine driven by natural language. It automates the entire pipeline from multi-source web crawling (with login state) to content cleaning, deduplication, and scheduled Word document generation.
一个基于自然语言驱动的招投标信息智能聚合引擎。实现从多源网站抓取（支持登录态）、内容清洗去重，到定时生成 Word 文档推送的全链路自动化闭环。

---

## 🎯 项目背景与痛点 | Background & Pain Points

**中文：**
企业在招投标信息获取中面临三大痛点：
1. **来源碎片化**：政企采购门户分散，大量高价值信息需登录获取。
2. **意图理解断层**：业务人员的复合自然语言需求（如“每天9点汇总最近3个月上海充电桩信息”）无法被传统工具解析。
3. **后处理成本高**：原始网页包含大量广告与冗余模板，无法直接作为决策依据。

**English:**
Enterprises face three major pain points in bidding information retrieval:
1. **Fragmented Sources**: Procurement portals are scattered, and high-value information often requires authentication.
2. **Intent Gap**: Complex natural language requests (e.g., "Summarize Shanghai EV charging station bids for the last 3 months at 9 AM daily") cannot be parsed by traditional tools.
3. **High Post-processing Costs**: Raw web pages contain massive ads and redundant templates, making them unusable for direct decision-making.

## 🚀 核心功能 | Core Features

- **自然语言意图解析 | NL Intent Parsing**: 精准提取主题关键词、区域、时间范围及执行频率。若包含频率则注册定时任务，否则立即触发。
- **多源登录态抓取 | Multi-source Auth Crawling**: 基于 Playwright 驱动，支持动态页面渲染与免费会员登录态抓取（覆盖至少2个网站，含1个登录站点）。
- **智能清洗与去重 | Cleaning & Deduplication**: 利用大模型剥离广告与模板，提取核心事实；基于 Redis 指纹去重，确保定时任务仅推送**增量内容**。
- **标准化文档输出 | Standardized Output**: 严格按 `{用户的问题}_{时间}.docx` 命名，利用 Apache POI 生成包含标题、时间、来源、核心内容、附件的结构化 Word 文件。

## 🏗️ 系统架构设计 | System Architecture

系统采用 Java + Python 混合微服务架构，由四大核心模块构成：
The system adopts a Java + Python hybrid microservices architecture, consisting of four core modules:

1. **意图解析与调度引擎 | Intent & Scheduling Engine (Java + LangChain4j + Quartz)**
   - 解析自然语言提取结构化参数，结合 Quartz 实现定时任务的持久化与精确调度。
2. **多源采集微服务 | Crawler Service (Python + FastAPI + Playwright)**
   - 应对复杂动态页面与登录态限制，提供高灵活性的数据抓取接口。
3. **清洗去重与提纯模块 | Processing & Purification (Java + Redis + LLM)**
   - URL MD5 指纹去重实现增量推送；大模型驱动的内容结构化提纯，确保事实一致性。
4. **文档生成模块 | Document Generator (Java + Apache POI)**
   - 严格按赛题要求生成标准化 Word 文档，支持本地存储与自动化推送。

## 🛠️ 技术栈 | Tech Stack

| 模块 | 技术 | 描述 |
| :--- | :--- | :--- |
| **核心后端** | `Java 17` `Spring Boot 3.x` | 系统主干框架，负责任务调度与流程编排 |
| **AI 服务** | `LangChain4j` `GLM-4 / DeepSeek` | 大语言模型接入，支持 Function Calling |
| **数据采集** | `Python 3.10+` `FastAPI` `Playwright` | 异步爬虫框架，突破动态渲染与登录限制 |
| **缓存与去重** | `Redis` | 存储内容指纹，实现高频增量去重 |
| **文档生成** | `Apache POI` | 生成符合规范 `.docx` 文件 |
| **任务调度** | `Quartz Scheduler` | 持久化定时任务，支持 Cron 表达式 |

## 📂 目录结构规划 | Project Structure

```text
├── bid-agent-core/         # Java核心服务 (意图解析, 调度, 清洗, 文档生成)
│   ├── src/main/java/...
│   └── pom.xml
├── bid-crawler-service/    # Python爬虫微服务 (Playwright驱动)
│   ├── main.py
│   ├── requirements.txt
│   └── spiders/...
├── docs/                   # 设计文档与操作手册
│   ├── 详设文档.md
│   └── 操作指南.md
└── README.md
```

## 📌 使用示例 | Usage Examples

**输入自然语言 | Input NL:**
> "{最近3个月}的{上海}区域内的{充电桩}招标信息都有哪些，请汇总后每天9:00发送给我"

**系统处理流程 | System Workflow:**
1. **解析意图**: 关键词=[充电桩], 区域=[上海], 时间=[最近3个月], 频率=[每天09:00]
2. **注册任务**: Quartz 注册每日 09:00 执行的 Cron 任务
3. **抓取与清洗**: 调用 Python 爬虫获取数据 -> 大模型清洗去广告 -> Redis 校验去重
4. **生成文档**: 生成 `最近3个月的上海区域内的充电桩招标信息都有哪些_202604070900.docx`

## 📈 开发进度 | Roadmap

- [x] 业务场景调研与架构设计
- [ ] Java Agent 意图解析与 Quartz 调度模块开发
- [ ] Python Playwright 登录态爬虫开发
- [ ] 增量去重与 LLM 清洗闭环测试
- [ ] Word 文档生成与命名规范化
- [ ] 赛题最终验收与 Demo 视频录制

## 📄 License
暂未确定
