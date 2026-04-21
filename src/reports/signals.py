"""
报告模块信号
"""
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from reports.interface.export_utils import build_brief_from_markdown
from reports.models.report import Report
from research.models.analysis_result import AnalysisResult


@receiver(pre_save, sender=Report)
def populate_brief_content(sender, instance: Report, **kwargs):
    """在保存报告前自动补齐简版内容。"""
    if instance.content_markdown and not (instance.content_brief or '').strip():
        instance.content_brief = build_brief_from_markdown(instance)


@receiver(post_save, sender=AnalysisResult)
def generate_or_update_report_from_analysis(sender, instance: AnalysisResult, created: bool, **kwargs):
    """分析结果落库后自动生成或更新报告。"""
    if not created:
        return

    task = instance.task
    summary = (instance.conclusion or '').strip()[:500] or f"{task.title} 分析结果摘要"
    raw_output = instance.raw_output or {}
    full_markdown = (
        raw_output.get('report_markdown')
        or raw_output.get('markdown')
        or raw_output.get('content_markdown')
        or _build_markdown_from_analysis(task.title, summary, instance.conclusion, raw_output)
    )

    latest_report = Report.objects.filter(task=task, is_latest=True).order_by('-version', '-created_at').first()
    next_version = (latest_report.version + 1) if latest_report else 1

    if latest_report:
        latest_report.is_latest = False
        latest_report.save(update_fields=['is_latest'])

    Report.objects.create(
        task=task,
        title=raw_output.get('title') or task.title,
        summary=summary,
        content_markdown=full_markdown,
        content_brief=raw_output.get('content_brief') or '',
        version=next_version,
        is_latest=True,
    )


def _build_markdown_from_analysis(title: str, summary: str, conclusion: str, raw_output: dict) -> str:
    sections = [
        f"# {title}",
        "",
        "## 摘要",
        summary or "暂无摘要。",
        "",
        "## 分析结论",
        (conclusion or "暂无分析结论。").strip(),
    ]

    key_points = raw_output.get('key_points') or raw_output.get('highlights') or []
    if key_points:
        sections.extend(["", "## 关键要点"])
        sections.extend([f"- {item}" for item in key_points if item])

    risks = raw_output.get('risks') or []
    if risks:
        sections.extend(["", "## 风险提示"])
        sections.extend([f"- {item}" for item in risks if item])

    return "\n".join(sections).strip() + "\n"
