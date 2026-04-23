"""
8Feet Django Settings
参照 example-backend/trebuchet/settings.py 结构，使用 YAML 配置文件
"""
import os
import yaml
from dotenv import load_dotenv

# ============================================================
# 配置文件加载
# ============================================================
# 项目根目录（src/ 的父目录，即项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))

_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.yaml')

# 如果 config.yaml 不存在，则尝试加载 config.example.yaml
if not os.path.exists(_CONFIG_PATH):
    _CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.example.yaml')

with open(_CONFIG_PATH, 'r', encoding='utf-8') as stream:
    _YAML_CONFIG = yaml.safe_load(stream)

# ============================================================
# 基础路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# 安全配置
# ============================================================
SECRET_KEY = _YAML_CONFIG['DjangoSecretKey']
DEBUG = _YAML_CONFIG.get('Debug', False)
ALLOWED_HOSTS = ['*']

# ============================================================
# 应用注册 — 5 个业务 App + Django Channels
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # 第三方
    'corsheaders',
    'channels',
    # 业务模块 (按依赖顺序)
    'users',
    'llm_manager',
    'research',
    'reports',
    'analytics',
]

# ============================================================
# 中间件
# ============================================================
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CORS_ALLOW_ALL_ORIGINS = True
CORS_EXPOSE_HEADERS = ['Content-Disposition', 'Date']

# ============================================================
# URL 与模板
# ============================================================
ROOT_URLCONF = 'eightfeet.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'eightfeet.wsgi.application'
ASGI_APPLICATION = 'eightfeet.asgi.application'

# ============================================================
# 数据库 — PostgreSQL 13
# ============================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': _YAML_CONFIG['DatabaseName'],
        'USER': _YAML_CONFIG['DatabaseUser'],
        'PASSWORD': _YAML_CONFIG['DatabasePassword'],
        'HOST': _YAML_CONFIG['DatabaseHost'],
        'PORT': _YAML_CONFIG['DatabasePort'],
    }
}

# ============================================================
# 密码验证
# ============================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ============================================================
# 国际化
# ============================================================
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# ============================================================
# 静态文件
# ============================================================
STATIC_URL = '/static/'

# ============================================================
# 缓存 — Redis 6.2
# ============================================================
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://{}/{}'.format(
            _YAML_CONFIG['RedisAddress'],
            _YAML_CONFIG.get('RedisDatabase', 0)
        ),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PASSWORD': _YAML_CONFIG.get('RedisPassword', '')
        }
    }
}

# ============================================================
# Django Channels — WebSocket 支持 (全流程监控)
# ============================================================
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [('127.0.0.1', 6379)],
        },
    },
}

# ============================================================
# Minio 对象存储 — 报告文件 (PDF/Word)
# ============================================================
S3_SECRET_ID = _YAML_CONFIG.get('S3SecretId', '')
S3_SECRET_KEY = _YAML_CONFIG.get('S3SecretKey', '')
S3_ADDRESS = _YAML_CONFIG.get('S3Address', 'localhost:9000')
S3_BUCKET_REPORTS = _YAML_CONFIG.get('S3BucketReports', 'eightfeet-reports')
S3_SSL = _YAML_CONFIG.get('S3UseSSL', False)

# ============================================================
# 邮件 (动态提醒推送)
# ============================================================
MAIL_SMTP_USERNAME = _YAML_CONFIG.get('SmtpUsername', '')
MAIL_SMTP_FROM = _YAML_CONFIG.get('SmtpFrom', '')
MAIL_SMTP_PASSWORD = _YAML_CONFIG.get('SmtpPassword', '')
MAIL_SMTP_HOST = _YAML_CONFIG.get('SmtpHost', '')
MAIL_SMTP_PORT = _YAML_CONFIG.get('SmtpPort', 465)

# ============================================================
# 测试运行器
# ============================================================
# Note: django-nose removed due to Python 3.12 incompatibility (uses removed 'imp' module)
