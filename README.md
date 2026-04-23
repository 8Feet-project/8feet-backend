# 8Feet 后端部署与开发指南

## 项目简介

**8Feet 商业对象智能深度调研分析平台** 的后端服务，基于 Django，采用 5 个独立 Django App 的模块化架构。

## 快速开始

### 1. 环境要求
- [uv](https://docs.astral.sh/uv/)
- Docker

### 2. 安装依赖
```bash
uv sync --python 3.12
```

### 3. 配置模型环境变量
后端支持从项目根目录 `.env` 加载 AI 模型配置，可参考 `.env.example`：
```env
MODEL_NAME=glm-5.1
MODEL_API_KEY=your-api-key
MODEL_BASE_URL=https://api.modelarts-maas.com/v2
```

### 4. 一键初始化
Windows:
```powershell
.\scripts\init_db.ps1 # win未验证
```
Linux:
```bash
./scripts/init_db.sh # linux已验证
```
自动完成：
- 启动 PostgreSQL 13 (端口 5432)
- 启动 Redis 6.2 (端口 6379)
- 启动 Minio (端口 9000/9001)
- 执行 Django 数据库迁移

### 5. 运行开发服务器
```bash
uv run python src/manage.py runserver
```

## 模块架构

| 模块 | URL 前缀 | 说明 |
|:-----|:---------|:-----|
| `users` | `/api/users/` | 用户登录/注册/权限管理 |
| `llm_manager` | `/api/llm/` | 大模型配置 CRUD |
| `research` | `/api/research/` | 调研任务发起/状态监控 |
| `reports` | `/api/reports/` | 报告查看/导出/追问 |
| `analytics` | `/api/analytics/` | 统计看板/收藏/提醒 |

每个模块内部结构：
- **api/**: HTTP 视图层，使用 `@response_wrapper`、`@jwt_auth()` 装饰器
- **interface/**: 纯业务逻辑层，不涉及 HTTP
- **models/**: 数据库模型定义

## 技术栈
- Django + Django Channels
- PostgreSQL 13 + Redis 6.2 + Minio
- JWT 认证
- Python 3.12+ (uv 管理依赖)
