"""报告文件本地存储工具。"""
from __future__ import annotations

from pathlib import Path
import shutil

from django.conf import settings


def report_export_root() -> Path:
    root = Path(getattr(settings, "REPORT_EXPORT_ROOT", Path(getattr(settings, "BASE_DIR", ".")).parent / "generated_reports"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def store_report_file(local_path: str, object_name: str | None = None) -> str:
    """保存导出文件到后端本地目录，返回相对存储路径。"""
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"导出文件不存在: {local_path}")

    relative_path = _safe_relative_path(object_name or path.name)
    destination = report_export_root() / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() != destination.resolve():
        shutil.copy2(path, destination)
    return relative_path.as_posix()


def report_file_path(storage_path: str) -> Path:
    relative_path = _safe_relative_path(storage_path)
    file_path = (report_export_root() / relative_path).resolve()
    root = report_export_root()
    if root != file_path and root not in file_path.parents:
        raise ValueError("非法的导出文件路径")
    return file_path


def build_report_download_url(export_id: int | str) -> str:
    return f"/api/v1/reports/exports/{export_id}/download"


def _safe_relative_path(value: str) -> Path:
    cleaned = str(value or "").strip().lstrip("/\\")
    relative_path = Path(cleaned)
    if not cleaned or relative_path.is_absolute() or any(part in ("", ".", "..") for part in relative_path.parts):
        raise ValueError("非法的导出文件路径")
    return relative_path
