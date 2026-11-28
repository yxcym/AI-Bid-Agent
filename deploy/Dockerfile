# 部署形态：后端镜像（仅 uvicorn，前端产物由 nginx 容器托管）
# 状态：本机已实测（Docker Desktop 29.9.3 / compose v5.7.1，2026-11-27）
# 用法：docker build -f deploy/Dockerfile -t ai-bid-agent .
# 安全：config/secrets.yaml 由根目录 .dockerignore 排除，绝不烤进镜像；
#       运行时由 compose 以只读卷挂入，或改用环境变量 BIDAGENT_SMTP_* 注入。

FROM python:3.12-slim

WORKDIR /srv/bidagent

# 先装依赖再拷代码，利用层缓存：代码改动不触发重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# playwright 引擎的浏览器（ccgp 等反爬站点用）。
# 坑位一：1.65 的驱动 node 启动就要 libatomic.so.1，而 --with-deps 是先跑驱动再装依赖，
#         所以 libatomic1 必须提前手动装；
# 坑位二：1.49 旧版的 --with-deps 依赖清单引用了 Debian 已改名的字体包（ttf-unifont），故锁 1.65.2；
# 坑位三：官方 CDN（azureedge）境内构建时常 TLS 断连，切 npmmirror 二进制镜像
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
RUN python -m playwright install --with-deps chromium

COPY app ./app
COPY config ./config

EXPOSE 8000

# data/ 与 output/ 由应用启动时自动创建；要持久化请在 compose 里挂卷
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
