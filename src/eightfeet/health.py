import socket

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from shared.utils import (
    ErrorCode,
    failed_api_response,
    response_wrapper,
    success_api_response,
)


def _check_db():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return True
    except Exception:
        return False


def _check_redis():
    try:
        key = 'healthz:ping'
        cache.set(key, 'pong', timeout=3)
        return cache.get(key) == 'pong'
    except Exception:
        return False


def _check_minio():
    try:
        host, port = settings.S3_ADDRESS.split(':', 1)
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except Exception:
        return False


@response_wrapper
def healthz(_request):
    return success_api_response(
        {
            'status': 'ok',
            'service': '8feet-backend',
            'timestamp': timezone.now().isoformat(),
        }
    )


@response_wrapper
def readyz(_request):
    checks = {
        'database': _check_db(),
        'redis': _check_redis(),
        'minio': _check_minio(),
    }
    is_ready = all(checks.values())
    payload = {
        'status': 'ready' if is_ready else 'not_ready',
        'checks': checks,
        'timestamp': timezone.now().isoformat(),
    }
    if is_ready:
        return success_api_response(payload)
    return failed_api_response(
        ErrorCode.SERVICE_UNAVAILABLE,
        'not_ready',
        data=payload,
    )
