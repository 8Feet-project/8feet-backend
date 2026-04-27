from __future__ import annotations

import json
from typing import Any

from research.interface.thread_codec import json_safe
from research.models import ResearchTask


def build_cross_model_research_prompt(task: ResearchTask, base_prompt: str) -> str:
    return (
        "你现在是多模型交叉验证中的一个独立调研线程。"
        "请不要参考其他模型的输出，也不要等待外部人工反馈。"
        "请独立完成完整调研，写入 Markdown 报告文件，并调用 present_report 展示该报告。\n\n"
        "报告文件路径建议使用:\n"
        "/mnt/user-data/outputs/model_research_report.md\n\n"
        "原始调研任务如下:\n"
        f"{base_prompt.strip()}"
    )


def build_cross_integrator_system_message() -> str:
    return (
        "你是 8Feet 多模型交叉验证智能整合 Lead Agent。"
        "你的任务不是重新做外部检索，而是读取多个模型独立调研线程的报告与证据，"
        "比较它们的一致结论、分歧、证据质量和遗漏点，产出一份更全面、更稳健的中文 Markdown 报告。"
        "所有来自模型报告的事实都要保留原报告中的 citation key；不要编造新的 citation key。"
        "最终必须把整合优化报告写入 /mnt/user-data/outputs/cross_validation_report.md，"
        "然后调用 present_report。"
    )


def build_cross_integrator_prompt(
    task_payload: dict[str, Any],
    model_results: list[dict[str, Any]],
    copy_manifests: list[dict[str, Any]],
) -> str:
    manifest_json = json.dumps(json_safe(copy_manifests), ensure_ascii=False, indent=2)
    results_overview = json.dumps(
        [
            {
                "status": item.get("status"),
                "model": item.get("model"),
                "thread_id": item.get("thread_id"),
                "child_task_id": item.get("child_task_id"),
                "child_report_id": item.get("child_report_id"),
                "summary": item.get("summary"),
                "report_paths": item.get("report_paths", []),
                "error": item.get("error", ""),
            }
            for item in model_results
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "请基于以下多模型调研结果完成智能整合优化。\n\n"
        "调研对象:\n"
        f"- 标题: {task_payload.get('title')}\n"
        f"- 对象: {task_payload.get('object_name')}\n"
        f"- 类型: {task_payload.get('object_type')}\n\n"
        "每个模型线程的沙箱已经复制到当前线程 workspace 下。"
        "请优先读取 copied_path 指向的 presented report；必要时再阅读同目录下的 evidence、outputs 或 workspace 文件。\n\n"
        "复制清单:\n"
        f"```json\n{manifest_json}\n```\n\n"
        "模型输出概览:\n"
        f"```json\n{results_overview}\n```\n\n"
        "输出要求:\n"
        "1. 提炼多模型一致支持的核心结论。\n"
        "2. 标出模型间分歧、证据冲突或只有单一模型支持的观点。\n"
        "3. 对证据质量和缺口做判断，必要时说明哪些结论需要人工复核。\n"
        "4. 形成整合优化后的最终调研参考报告。\n"
        "5. 将报告写入 /mnt/user-data/outputs/cross_validation_report.md 并调用 present_report。"
    )
