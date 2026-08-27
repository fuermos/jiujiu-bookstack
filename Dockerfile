# jiujiu-bookstack 全功能镜像
# 包含: pipeline + MCP server + DeepAgent + Streamlit Web UI + 依赖
#
# 构建:
#   docker build -t jiujiu-bookstack:latest .
#
# 一键启动 (推荐):
#   docker-compose up -d
#   # 然后访问 http://localhost:8501
#
# 单独运行 (pipeline):
#   docker run --rm \
#     -v $(pwd)/config:/app/config \
#     -v $(pwd)/books:/app/books \
#     --network host \
#     jiujiu-bookstack python scripts/pipeline.py books/
#
# 单独运行 (MCP server, stdio):
#   docker run -i --rm \
#     -v $(pwd)/config:/app/config \
#     --network host \
#     jiujiu-bookstack python scripts/mcp_server.py
#
# 单独运行 (DeepAgent 剧本杀 CLI):
#   docker run -it --rm \
#     -v $(pwd)/config:/app/config \
#     --network host \
#     jiujiu-bookstack python agent/deep_agent.py --book-id 384 --interactive
#
# 单独运行 (Web UI):
#   docker run -d --rm \
#     -p 8501:8501 \
#     -v $(pwd)/config:/app/config \
#     --network host \
#     jiujiu-bookstack streamlit run web/app.py --server.port 8501 --server.address 0.0.0.0

FROM python:3.11-slim

LABEL org.opencontainers.image.title="jiujiu-bookstack"
LABEL org.opencontainers.image.description="把 epub 书库变成可查询、可玩、可听的家庭知识库"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/fuermos/jiujiu-bookstack"

WORKDIR /app

# 系统依赖 (psycopg2 / ebooklib 需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖（先复制 requirements 利用层缓存）
COPY requirements.txt .
# 国内主机 PyPI 不通, 优先阿里云镜像, 歉备清华源 (铲屎官 2026-08-26 rebuild 加速)
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host "mirrors.aliyun.com pypi.tuna.tsinghua.edu.cn" && \
    pip install --no-cache-dir -r requirements.txt

# 项目代码
COPY scripts/ ./scripts/
COPY agent/ ./agent/
COPY web/ ./web/
COPY config/ ./config/
COPY docs/ ./docs/

# 运行时目录（挂载点）
RUN mkdir -p books data logs state mindmaps tts

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/scripts:/app/agent:/app/web

EXPOSE 8501

# 默认入口: 打印帮助（docker-compose 的 app 容器会覆盖）
CMD ["python", "scripts/pipeline.py", "--help"]