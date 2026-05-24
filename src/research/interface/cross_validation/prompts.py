from __future__ import annotations

import json
from typing import Any

from research.interface.prompt_contracts import (
    citation_discipline_requirements,
    object_type_research_requirements,
    report_format_requirements,
)
from research.interface.thread_codec import json_safe
from research.models import ResearchTask


def build_cross_validation_base_prompt(task: ResearchTask) -> str:
    search_params = json.dumps(
        json_safe(getattr(task, "search_params", {}) or {}),
        ensure_ascii=False,
        indent=2,
    )
    object_contract = object_type_research_requirements(getattr(task, "object_type", ""))
    return (
        "请围绕以下商业对象开展一次独立商业调研，并输出结构化 Markdown 详细报告与简版报告。\n\n"
        f"- 调研标题: {getattr(task, 'title', '')}\n"
        f"- 调研对象: {getattr(task, 'object_name', '')}\n"
        f"- 对象类型: {getattr(task, 'object_type', '')}\n\n"
        f"{object_contract}\n"
        "任务要求:\n"
        "1. 交叉验证固定自动推进；独立完成检索、证据读取、分析和报告定稿，不委托子代理。\n"
        "2. 必须优先覆盖上方对象类型专项调研框架，再补充通用商业分析维度。\n"
        "3. web_search 只用于发现候选网址；事实结论必须由 web_fetch 或结构化业务数据工具返回的 citation key 支撑。\n"
        "4. 证据不足时说明不确定性并完成报告，不要无限检索。\n\n"
        f"补充检索参数:\n```json\n{search_params}\n```"
    )


def build_cross_model_research_prompt(task: ResearchTask, base_prompt: str) -> str:
    base_text = base_prompt.strip()
    object_contract = object_type_research_requirements(getattr(task, "object_type", ""))
    report_contract = report_format_requirements(
        "/mnt/user-data/outputs/model_research_report.md",
        "/mnt/user-data/outputs/model_research_report_brief.md",
    )
    citation_contract = citation_discipline_requirements()
    object_contract_block = ""
    if "对象类型专项调研框架" not in base_text:
        object_contract_block = (
            "对象类型专项调研框架如下，必须优先覆盖后再补充通用商业分析维度:\n"
            f"{object_contract}\n\n"
        )
    return (
        "你现在是多模型交叉验证中的一个独立调研线程。"
        "请不要参考其他模型的输出；本流程固定自动推进。"
        "请独立完成完整调研，分别写入详细版与简版 Markdown 报告文件，并调用 present_report 同时展示两份报告。\n\n"
        "报告文件路径建议使用:\n"
        "- /mnt/user-data/outputs/model_research_report.md\n"
        "- /mnt/user-data/outputs/model_research_report_brief.md\n\n"
        f"{citation_contract}\n"
        f"{report_contract}\n"
        "本独立线程必须使用上方 model_research_report.md 和 model_research_report_brief.md 路径；"
        "原始任务里若出现其他报告路径，仅作为主任务默认要求，不适用于本线程。\n\n"
        f"{object_contract_block}"
        "原始调研任务如下:\n"
        f"{base_text}"
    )


def build_cross_integrator_system_message() -> str:
    report_contract = report_format_requirements(
        "/mnt/user-data/outputs/cross_validation_report.md",
        "/mnt/user-data/outputs/cross_validation_report_brief.md",
    )
    citation_contract = citation_discipline_requirements(integrator=True)
    return (
        "你是 8Feet 多模型交叉验证智能整合 Lead Agent。"
        "你的任务不是重新做外部检索，而是读取多个模型独立调研线程的报告与证据，"
        "比较它们的一致结论、分歧、证据质量和遗漏点，产出一份更全面、更稳健的中文 Markdown 报告。"
        f"\n{citation_contract}"
        f"\n{report_contract}"
        "最终必须把整合优化详细报告写入 /mnt/user-data/outputs/cross_validation_report.md，"
        "把简版报告写入 /mnt/user-data/outputs/cross_validation_report_brief.md，"
        "然后调用 present_report 同时提交两份报告。"
    )


def build_cross_integrator_prompt(
    task_payload: dict[str, Any],
    model_results: list[dict[str, Any]],
    copy_manifests: list[dict[str, Any]],
) -> str:
    manifest_json = json.dumps(json_safe(copy_manifests), ensure_ascii=False, indent=2)
    object_contract = object_type_research_requirements(str(task_payload.get("object_type") or ""))
    report_contract = report_format_requirements(
        "/mnt/user-data/outputs/cross_validation_report.md",
        "/mnt/user-data/outputs/cross_validation_report_brief.md",
    )
    citation_contract = citation_discipline_requirements(integrator=True)
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
        "对象类型专项核查框架:\n"
        f"{object_contract}\n\n"
        "每个模型线程的沙箱已经复制到当前线程 workspace 下。"
        "请优先读取 copied_path 指向的 presented report；必要时再阅读同目录下的 evidence、outputs 或 workspace 文件。\n\n"
        "复制清单:\n"
        f"```json\n{manifest_json}\n```\n\n"
        "模型输出概览:\n"
        f"```json\n{results_overview}\n```\n\n"
        "输出要求:\n"
        "1. 先按对象类型专项核查框架检查各模型报告是否覆盖关键维度。\n"
        "2. 提炼多模型一致支持的核心结论。\n"
        "3. 标出模型间分歧、证据冲突或只有单一模型支持的观点。\n"
        "4. 对证据质量和缺口做判断，必要时说明哪些结论需要人工复核。\n"
        "5. 形成整合优化后的最终调研参考报告。\n"
        "6. 严格遵守下方引用约束与最终报告格式交付要求。\n\n"
        f"{citation_contract}\n"
        f"{report_contract}"
    )
