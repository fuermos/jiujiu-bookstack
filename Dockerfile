# jiujiu-bookstack 全功能镜像
# 包含: pipeline + MCP server + DeepAgent + 依赖
#
# 构建:
#   docker build -t jiujiu-bookstack:latest .
#
# 运行 (pipeline):
#   docker run --rm \
#     -v $(pwd)/config:/app/config \
#     -v $(pwd)/books:/app/books \
#     --network host \
#     jiujiu-bookstack python scripts/pipeline.py books/
#
# 运行 (MCP server, stdio):
#   docker run -i --rm \
#     -v $(pwd)/config:/app/config \
#     --network host \
#     jiujiu-bookstack python scripts/mcp_server.py
#
# 运行 (DeepAgent 剧本杀):
#   docker run -it --rm \
#     -v $(pwd)/config:/app/config \
#     --network host \
#     jiujiu-bookstack python agent/deep_agent.py --book-id 384 --interactive

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
RUN pip install --no-cache-dir -r requirements.txt

# 项目代码
COPY scripts/ ./scripts/
COPY agent/ ./agent/
COPY config/ ./config/
COPY docs/ ./docs/

# 运行时目录（挂载点）
RUN mkdir -p books data logs state mindmaps tts

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/scripts:/app

# 默认入口: 打印帮助
CMD ["python", "scripts/pipeline.py", "--help"]
