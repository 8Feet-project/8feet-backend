# 8Feet 数据库初始化脚本 (PowerShell)
# 版本锁定: PostgreSQL 13, Redis 6.2

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  8Feet 后端 - 数据库初始化脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ============================================================
# 1. 检查 Docker 环境
# ============================================================
Write-Host "`n[1/5] 检查 Docker 状态..." -ForegroundColor Yellow
docker version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker 未运行，请先启动 Docker Desktop。"
    exit 1
}

# ============================================================
# 2. 启动基础服务 (PostgreSQL 13 + Redis 6.2 + Minio)
# ============================================================
Write-Host "[2/5] 启动基础服务 (PostgreSQL 13, Redis 6.2, Minio)..." -ForegroundColor Yellow
docker-compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker 服务启动失败。"
    exit 1
}

# ============================================================
# 3. 等待数据库就绪
# ============================================================
Write-Host "[3/5] 等待数据库就绪..." -ForegroundColor Yellow
$maxRetries = 30
$retryCount = 0
do {
    Start-Sleep -Seconds 2
    $retryCount++
    docker-compose exec -T db pg_isready -U admin -d eightfeet > $null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  数据库已就绪。" -ForegroundColor Green
        break
    }
    Write-Host "  等待中... ($retryCount/$maxRetries)"
} while ($retryCount -lt $maxRetries)

if ($retryCount -ge $maxRetries) {
    Write-Error "数据库未能在预期时间内就绪。"
    exit 1
}

# ============================================================
# 4. 复制配置文件
# ============================================================
Write-Host "[4/5] 检查配置文件..." -ForegroundColor Yellow
if (-not (Test-Path "config.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
    Write-Host "  已复制 config.example.yaml -> config.yaml，请按需修改。" -ForegroundColor Yellow
} else {
    Write-Host "  config.yaml 已存在。" -ForegroundColor Green
}

# ============================================================
# 5. 执行 Django 数据库迁移
# ============================================================
Write-Host "[5/5] 执行 Django 数据库迁移..." -ForegroundColor Yellow
uv run python src/manage.py makemigrations users llm_manager research reports analytics
uv run python src/manage.py migrate

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  初始化完成！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  PostgreSQL: localhost:5432" -ForegroundColor Cyan
    Write-Host "  Redis:      localhost:6379" -ForegroundColor Cyan
    Write-Host "  Minio:      http://localhost:9001 (管理界面)" -ForegroundColor Cyan
    Write-Host "`n  运行开发服务器: uv run python src/manage.py runserver" -ForegroundColor Cyan
} else {
    Write-Error "数据库迁移失败，请检查错误信息。"
}
