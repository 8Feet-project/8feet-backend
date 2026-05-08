"""
8Feet Django Settings
参照 YAML 配置结构，同时支持环境变量注入用于生产部署。
"""
import os
from urllib.parse import quote_plus

import yaml
from dotenv import load_dotenv


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _to_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_list(value, default=None):
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return list(default or [])


def _env(name, default=None):
    value = os.getenv(name)
    if value is None:
        return default
    return value


def _is_placeholder_secret(value):
    if not value:
        return True
    normalized = str(value).strip().lower()
    placeholder_markers = (
        'change_me',
        'replace_me',
        'example',
        'password123',
        'minioadmin',
        'django-insecure-',
    )
    return any(marker in normalized for marker in placeholder_markers)


# ============================================================
# 配置文件加载
# ============================================================
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.yaml')

if not os.path.exists(_CONFIG_PATH):
    _CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.example.yaml')

with open(_CONFIG_PATH, 'r', encoding='utf-8') as stream:
    _YAML_CONFIG = yaml.safe_load(stream) or {}

# ============================================================
# 基础路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# 安全配置
# ============================================================
SECRET_KEY = _env('DJANGO_SECRET_KEY', _YAML_CONFIG.get('DjangoSecretKey', ''))
if not SECRET_KEY:
    raise ValueError('DJANGO_SECRET_KEY / DjangoSecretKey 不能为空')

DEBUG = _to_bool(_env('DJANGO_DEBUG', _YAML_CONFIG.get('Debug', False)), False)
if not DEBUG and _is_placeholder_secret(SECRET_KEY):
    raise ValueError('生产环境禁止使用占位或弱 DJANGO_SECRET_KEY')

ALLOWED_HOSTS = _to_list(
    _env('DJANGO_ALLOWED_HOSTS', _YAML_CONFIG.get('AllowedHosts', ['127.0.0.1', 'localhost']))
)
CSRF_TRUSTED_ORIGINS = _to_list(
    _env('DJANGO_CSRF_TRUSTED_ORIGINS', _YAML_CONFIG.get('CsrfTrustedOrigins', []))
)

# ============================================================
# 应用注册
# ============================================================
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'channels',
    'users',
    'llm_manager',
    'research.apps.ResearchConfig',
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
#    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CORS_ALLOW_ALL_ORIGINS = _to_bool(
    _env('CORS_ALLOW_ALL_ORIGINS', _YAML_CONFIG.get('CorsAllowAllOrigins', DEBUG)),
    DEBUG,
)
CORS_ALLOWED_ORIGINS = _to_list(
    _env('CORS_ALLOWED_ORIGINS', _YAML_CONFIG.get('CorsAllowedOrigins', []))
)
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
# 数据库
# ============================================================
DB_HOST = _env('DB_HOST', _YAML_CONFIG.get('DatabaseHost', 'localhost'))
DB_PORT = _to_int(_env('DB_PORT', _YAML_CONFIG.get('DatabasePort', 5432)), 5432)
DB_USER = _env('DB_USER', _YAML_CONFIG.get('DatabaseUser', 'admin'))
DB_PASSWORD = _env('DB_PASSWORD', _YAML_CONFIG.get('DatabasePassword', ''))
DB_NAME = _env('DB_NAME', _YAML_CONFIG.get('DatabaseName', 'eightfeet'))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': DB_NAME,
        'USER': DB_USER,
        'PASSWORD': DB_PASSWORD,
        'HOST': DB_HOST,
        'PORT': DB_PORT,
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
STATIC_ROOT = _env('STATIC_ROOT', _YAML_CONFIG.get('StaticRoot', '/var/www/8feet/staticfiles'))

# ============================================================
# 反向代理与 Cookie 安全配置
# ============================================================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

SESSION_COOKIE_SECURE = _to_bool(
    _env('SESSION_COOKIE_SECURE', _YAML_CONFIG.get('SessionCookieSecure', not DEBUG)),
    not DEBUG,
)
SESSION_COOKIE_HTTPONLY = _to_bool(
    _env('SESSION_COOKIE_HTTPONLY', _YAML_CONFIG.get('SessionCookieHttpOnly', True)),
    True,
)
SESSION_COOKIE_SAMESITE = _env(
    'SESSION_COOKIE_SAMESITE',
    _YAML_CONFIG.get('SessionCookieSameSite', 'Lax'),
)
CSRF_COOKIE_SECURE = _to_bool(
    _env('CSRF_COOKIE_SECURE', _YAML_CONFIG.get('CsrfCookieSecure', not DEBUG)),
    not DEBUG,
)
CSRF_COOKIE_DOMAIN = _env('CSRF_COOKIE_DOMAIN', _YAML_CONFIG.get('CsrfCookieDomain'))
if CSRF_COOKIE_DOMAIN == '':
    CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_HTTPONLY = _to_bool(
    _env('CSRF_COOKIE_HTTPONLY', _YAML_CONFIG.get('CsrfCookieHttpOnly', False)),
    False,
)
CSRF_COOKIE_SAMESITE = _env(
    'CSRF_COOKIE_SAMESITE',
    _YAML_CONFIG.get('CsrfCookieSameSite', 'Lax'),
)
CSRF_USE_SESSIONS = _to_bool(
    _env('CSRF_USE_SESSIONS', _YAML_CONFIG.get('CsrfUseSessions', False)),
    False,
)

SECURE_SSL_REDIRECT = _to_bool(
    _env('SECURE_SSL_REDIRECT', _YAML_CONFIG.get('SecureSSLRedirect', not DEBUG)),
    not DEBUG,
)
SECURE_REDIRECT_EXEMPT = [
    r'^healthz$',
    r'^readyz$',
]
SECURE_HSTS_SECONDS = _to_int(
    _env('SECURE_HSTS_SECONDS', _YAML_CONFIG.get('SecureHstsSeconds', 31536000 if not DEBUG else 0)),
    31536000 if not DEBUG else 0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _to_bool(
    _env(
        'SECURE_HSTS_INCLUDE_SUBDOMAINS',
        _YAML_CONFIG.get('SecureHstsIncludeSubdomains', not DEBUG),
    ),
    not DEBUG,
)
SECURE_HSTS_PRELOAD = _to_bool(
    _env('SECURE_HSTS_PRELOAD', _YAML_CONFIG.get('SecureHstsPreload', not DEBUG)),
    not DEBUG,
)
SECURE_CONTENT_TYPE_NOSNIFF = _to_bool(
    _env('SECURE_CONTENT_TYPE_NOSNIFF', _YAML_CONFIG.get('SecureContentTypeNosniff', not DEBUG)),
    not DEBUG,
)
SECURE_REFERRER_POLICY = _env(
    'SECURE_REFERRER_POLICY',
    _YAML_CONFIG.get('SecureReferrerPolicy', 'strict-origin-when-cross-origin'),
)
X_FRAME_OPTIONS = _env(
    'X_FRAME_OPTIONS',
    _YAML_CONFIG.get('XFrameOptions', 'DENY'),
)

# ============================================================
# Redis
# ============================================================
REDIS_ADDRESS = _env('REDIS_ADDRESS', _YAML_CONFIG.get('RedisAddress', 'localhost:6379'))
REDIS_PASSWORD = _env('REDIS_PASSWORD', _YAML_CONFIG.get('RedisPassword', ''))
REDIS_DATABASE = _to_int(_env('REDIS_DATABASE', _YAML_CONFIG.get('RedisDatabase', 0)), 0)

if REDIS_PASSWORD:
    REDIS_LOCATION = (
        f"redis://:{quote_plus(REDIS_PASSWORD)}@{REDIS_ADDRESS}/{REDIS_DATABASE}"
    )
else:
    REDIS_LOCATION = f"redis://{REDIS_ADDRESS}/{REDIS_DATABASE}"

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_LOCATION,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PASSWORD': REDIS_PASSWORD,
        },
    }
}

# ============================================================
# Django Channels
# ============================================================
CHANNEL_REDIS_URL = _env('CHANNEL_REDIS_URL')
if not CHANNEL_REDIS_URL:
    channel_redis_address = _env(
        'CHANNEL_REDIS_ADDRESS',
        _YAML_CONFIG.get('ChannelRedisAddress', REDIS_ADDRESS),
    )
    channel_redis_password = _env(
        'CHANNEL_REDIS_PASSWORD',
        _YAML_CONFIG.get('ChannelRedisPassword', REDIS_PASSWORD),
    )
    channel_redis_db = _to_int(
        _env('CHANNEL_REDIS_DATABASE', _YAML_CONFIG.get('ChannelRedisDatabase', REDIS_DATABASE)),
        REDIS_DATABASE,
    )
    if channel_redis_password:
        CHANNEL_REDIS_URL = (
            f"redis://:{quote_plus(channel_redis_password)}@{channel_redis_address}/{channel_redis_db}"
        )
    else:
        CHANNEL_REDIS_URL = f"redis://{channel_redis_address}/{channel_redis_db}"

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [CHANNEL_REDIS_URL],
        },
    },
}

# ============================================================
# Celery
# ============================================================
CELERY_BROKER_URL = _env('CELERY_BROKER_URL', _YAML_CONFIG.get('CeleryBrokerUrl', REDIS_LOCATION))
CELERY_RESULT_BACKEND = _env('CELERY_RESULT_BACKEND', _YAML_CONFIG.get('CeleryResultBackend', REDIS_LOCATION))
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = _to_int(
    _env('CELERY_TASK_TIME_LIMIT', _YAML_CONFIG.get('CeleryTaskTimeLimit', 600)),
    600,
)

# ============================================================
# Minio 对象存储
# ============================================================
S3_SECRET_ID = _env('S3_SECRET_ID', _YAML_CONFIG.get('S3SecretId', ''))
S3_SECRET_KEY = _env('S3_SECRET_KEY', _YAML_CONFIG.get('S3SecretKey', ''))
S3_ADDRESS = _env('S3_ADDRESS', _YAML_CONFIG.get('S3Address', 'localhost:9000'))
S3_BUCKET_REPORTS = _env('S3_BUCKET_REPORTS', _YAML_CONFIG.get('S3BucketReports', 'eightfeet-reports'))
S3_SSL = _to_bool(_env('S3_USE_SSL', _YAML_CONFIG.get('S3UseSSL', False)), False)

# ============================================================
# 邮件
# ============================================================
MAIL_SMTP_USERNAME = _env('SMTP_USERNAME', _YAML_CONFIG.get('SmtpUsername', ''))
MAIL_SMTP_FROM = _env('SMTP_FROM', _YAML_CONFIG.get('SmtpFrom', ''))
MAIL_SMTP_PASSWORD = _env('SMTP_PASSWORD', _YAML_CONFIG.get('SmtpPassword', ''))
MAIL_SMTP_HOST = _env('SMTP_HOST', _YAML_CONFIG.get('SmtpHost', ''))
MAIL_SMTP_PORT = _to_int(_env('SMTP_PORT', _YAML_CONFIG.get('SmtpPort', 465)), 465)
smtp_use_ssl_env = _env('SMTP_USE_SSL')
smtp_use_tls_env = _env('SMTP_USE_TLS')
MAIL_SMTP_USE_SSL = _to_bool(
    smtp_use_ssl_env,
    _YAML_CONFIG.get('SmtpUseSsl', MAIL_SMTP_PORT == 465),
)
MAIL_SMTP_USE_TLS = _to_bool(
    smtp_use_tls_env,
    _YAML_CONFIG.get('SmtpUseTls', MAIL_SMTP_PORT == 587),
)
MAIL_SMTP_TIMEOUT = _to_int(_env('SMTP_TIMEOUT', _YAML_CONFIG.get('SmtpTimeout', 15)), 15)
MAIL_SMTP_USE_LOCALTIME = _to_bool(
    _env('SMTP_USE_LOCALTIME', _YAML_CONFIG.get('SmtpUseLocaltime', True)),
    True,
)

if MAIL_SMTP_USE_SSL and MAIL_SMTP_USE_TLS:
    raise ValueError('SMTP_USE_SSL / SmtpUseSsl 与 SMTP_USE_TLS / SmtpUseTls 不能同时为 true')

# Django 标准邮件配置
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = MAIL_SMTP_HOST
EMAIL_PORT = MAIL_SMTP_PORT
EMAIL_HOST_USER = MAIL_SMTP_USERNAME
EMAIL_HOST_PASSWORD = MAIL_SMTP_PASSWORD
EMAIL_USE_SSL = MAIL_SMTP_USE_SSL
EMAIL_USE_TLS = MAIL_SMTP_USE_TLS
EMAIL_TIMEOUT = MAIL_SMTP_TIMEOUT
EMAIL_USE_LOCALTIME = MAIL_SMTP_USE_LOCALTIME
DEFAULT_FROM_EMAIL = MAIL_SMTP_FROM or MAIL_SMTP_USERNAME

# ============================================================
# 日志（结构化输出 + 按天轮转）
# ============================================================
LOG_DIR = _env('LOG_DIR', _YAML_CONFIG.get('LogDir', os.path.join(_PROJECT_ROOT, 'logs')))
LOG_LEVEL = _env('LOG_LEVEL', _YAML_CONFIG.get('LogLevel', 'INFO')).upper()
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            'format': (
                '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
                '"module":"%(module)s","line":%(lineno)d,"message":"%(message)s"}'
            ),
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
        'app_file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'app.log'),
            'when': 'midnight',
            'backupCount': 14,
            'formatter': 'json',
            'encoding': 'utf-8',
        },
    },
    'root': {
        'handlers': ['console', 'app_file'],
        'level': LOG_LEVEL,
    },
}
