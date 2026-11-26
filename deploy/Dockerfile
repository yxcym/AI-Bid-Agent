# 部署形态示例：后端镜像（仅 uvicorn，前端产物由 nginx 容器托管）
# 状态：未在本机验证（本机无Docker）。启用前先 docker build 跑通。
# 用法：docker build -f deploy/Dockerfile -t ai-bid-agent .

FROM python:3.12-slim

WORKDIR /srv/bidagent

# 先装依赖再拷代码，利用层缓存：代码改动不触发重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

EXPOSE 8000

# data/ 与 output/ 由应用启动时自动创建；要持久化请在 compose 里挂卷
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
