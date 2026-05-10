#!/usr/bin/env bash
# 8Feet 数据库初始化脚本 (Linux / macOS)
# 版本锁定: PostgreSQL 17.9, Redis 7.4.8

set -e

# 获取脚本所在目录并切换到项目根目录
cd "$(dirname "$0")/.."

CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  8Feet 后端 - 数据库初始化脚本${NC}"
echo -e "${CYAN}========================================${NC}"

# ============================================================
# 1. 检查 Docker 环境并设置 Compose 命令
# ============================================================
echo -e "\n${YELLOW}[1/5] 检查 Docker 状态...${NC}"
if ! docker version > /dev/null 2>&1; then
    echo -e "${RED}Docker 未运行，请先启动 Docker。${NC}"
    exit 1
fi

# 自动识别 docker-compose (V1) 或 docker compose (V2)
if docker compose version > /dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
elif docker-compose version > /dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}未找到 Docker Compose。请确保已安装 docker-compose 插件或工具。${NC}"
    exit 1
fi
echo -e "  检测到 Compose 命令: ${CYAN}$DOCKER_COMPOSE_CMD${NC}"

# ============================================================
# 2. 启动基础服务 (PostgreSQL 17.9 + Redis 7.4.8 + Minio)
# ============================================================
echo -e "${YELLOW}[2/5] 启动基础服务 (PostgreSQL 17.9, Redis 7.4.8, Minio)...${NC}"
$DOCKER_COMPOSE_CMD up -d db redis minio minio-init
if [ $? -ne 0 ]; then
    echo -e "${RED}Docker 服务启动失败。${NC}"
    exit 1
fi

# ============================================================
# 3. 等待数据库就绪
# ============================================================
echo -e "${YELLOW}[3/5] 等待数据库就绪...${NC}"
MAX_RETRIES=30
RETRY_COUNT=0
until $DOCKER_COMPOSE_CMD exec -T db pg_isready -U admin -d eightfeet > /dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "${RED}数据库未能在预期时间内就绪。${NC}"
        exit 1
    fi
    echo "  等待中... ($RETRY_COUNT/$MAX_RETRIES)"
    sleep 2
done
echo -e "${GREEN}  数据库已就绪。${NC}"

# ============================================================
# 4. 复制配置文件
# ============================================================
echo -e "${YELLOW}[4/5] 检查配置文件...${NC}"
if [ ! -f "config.yaml" ]; then
    cp config.example.yaml config.yaml
    echo -e "${YELLOW}  已复制 config.example.yaml -> config.yaml，请按需修改。${NC}"
else
    echo -e "${GREEN}  config.yaml 已存在。${NC}"
fi

# ============================================================
# 5. 执行 Django 数据库迁移
# ============================================================
echo -e "${YELLOW}[5/5] 执行 Django 数据库迁移...${NC}"
uv run python src/manage.py migrate

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  初始化完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  PostgreSQL: localhost:48882${NC}"
echo -e "${CYAN}  Redis:      localhost:48883${NC}"
echo -e "${CYAN}  Minio:      http://localhost:48885 (管理界面)${NC}"
echo -e "\n${CYAN}  运行开发服务器: uv run python src/manage.py runserver 127.0.0.1:48881${NC}"
