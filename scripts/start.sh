#!/usr/bin/env bash
# jiujiu-bookstack 一键启动脚本
#
# 步骤:
#   1. 检查依赖 (docker / python / miniconda)
#   2. 创建 conda 环境
#   3. 启动 PostgreSQL (Docker)
#   4. 初始化数据库 schema
#   5. 安装依赖
#   6. 配置 config.yaml (从 example)
#   7. 启动 MCP server
#
# 用法:
#   bash scripts/start.sh           # 默认配置
#   bash scripts/start.sh --skip-db # 跳过 Docker, 用现有 PG

set -e

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

SKIP_DB=false
for arg in "$@"; do
    case $arg in
        --skip-db) SKIP_DB=true ;;
    esac
done

# ============ 1. 检查依赖 ============
info "检查依赖..."
command -v python3 >/dev/null 2>&1 || err "python3 未安装"
command -v docker >/dev/null 2>&1 || warn "docker 未安装（需要手动启动 PG）"
command -v conda >/dev/null 2>&1 && CONDA_AVAILABLE=true || CONDA_AVAILABLE=false

# ============ 2. conda 环境 ============
if [ "$CONDA_AVAILABLE" = true ]; then
    info "创建 conda 环境 bookstack (Python 3.11)..."
    conda create -n bookstack python=3.11 -y 2>/dev/null || true
    source activate bookstack
else
    warn "conda 未安装，使用 venv (推荐先装 miniconda: https://docs.conda.io/en/latest/miniconda.html)"
    python3 -m venv .venv
    source .venv/bin/activate
fi

# ============ 3. 启动 PG (Docker) ============
if [ "$SKIP_DB" = false ] && command -v docker >/dev/null 2>&1; then
    info "启动 PostgreSQL (Docker)..."
    if ! docker ps | grep -q jiujiu-postgres; then
        docker run -d \
            --name jiujiu-postgres \
            -e POSTGRES_PASSWORD=*** \
            -e POSTGRES_DB=jiujiu_mind \
            -p 15433:5432 \
            -v pgdata:/var/lib/postgresql/data \
            pgvector/pgvector:pg16 \
            || err "Docker 启动 PG 失败"
        info "等待 PG 就绪..."
        sleep 3
    else
        info "PG 容器已在运行"
    fi
fi

# ============ 4. 初始化数据库 ============
info "初始化数据库 schema..."
PGPASSWORD=${DB_PASSWORD:-changeme} psql -h localhost -p 15433 -U admin -d jiujiu_mind -f "$PROJECT_ROOT/config/init_db.sql" 2>/dev/null || \
    warn "schema 初始化失败（请手动执行 psql ... < init_db.sql）"

# ============ 5. 安装依赖 ============
info "安装 Python 依赖..."
pip install -r requirements.txt

# ============ 6. 配置 ============
if [ ! -f config/config.yaml ]; then
    info "创建 config/config.yaml (从 example 复制)"
    cp config/config.example.yaml config/config.yaml
    warn "⚠️  请编辑 config/config.yaml 填入 LLM API Key"
    warn "⚠️  然后再运行: bash scripts/start.sh --mcp"
    exit 0
fi

# ============ 7. 启动 MCP ============
if [[ "$*" == *"--mcp"* ]]; then
    info "启动 MCP server (stdio)..."
    python scripts/mcp_server.py
fi

info "✅ 启动完成！"
echo ""
echo "下一步:"
echo "  1. 编辑 config/config.yaml 填 API Key"
echo "  2. 把书放进 books/ 目录"
echo "  3. 跑流水线: python scripts/pipeline.py books/"
echo "  4. 启动 MCP: python scripts/mcp_server.py"
