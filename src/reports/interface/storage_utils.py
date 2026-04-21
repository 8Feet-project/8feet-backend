"""
报告文件存储工具
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from minio import Minio


def _build_minio_client() -> Minio:
    secure = bool(getattr(settings, 'S3_SSL', False))
    return Minio(
        getattr(settings, 'S3_ADDRESS'),
        access_key=getattr(settings, 'S3_SECRET_ID'),
        secret_key=getattr(settings, 'S3_SECRET_KEY'),
        secure=secure,
    )


def upload_report_file(local_path: str, object_name: str | None = None) -> tuple[str, str]:
    """上传报告文件到 MinIO，失败时回退为本地路径。"""
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"导出文件不存在: {local_path}")

    object_key = object_name or path.name
    if not getattr(settings, 'S3_SECRET_ID', None) or not getattr(settings, 'S3_SECRET_KEY', None):
        return str(path), str(path)

    try:
        client = _build_minio_client()
        bucket = getattr(settings, 'S3_BUCKET_REPORTS')
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        client.fput_object(bucket, object_key, str(path))

        scheme = 'https' if getattr(settings, 'S3_SSL', False) else 'http'
        base = f"{scheme}://{getattr(settings, 'S3_ADDRESS')}".rstrip('/')
        return object_key, f"{base}/{bucket}/{_normalize_object_key(object_key)}"
    except Exception:
        return str(path), str(path)


def _normalize_object_key(object_key: str) -> str:
    parts = urlsplit(object_key)
    return parts.path.lstrip('/') if parts.scheme else object_key.lstrip('/')
