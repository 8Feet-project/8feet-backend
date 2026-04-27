"""Shared prompt contracts for research agents."""
from __future__ import annotations

REPORT_SECTION_HEADINGS = (
    "摘要",
    "核心发现",
    "关键证据",
    "风险与不确定性",
    "结论与建议",
)


def report_format_requirements(report_path: str | None = None) -> str:
    """Return the required final report delivery contract."""
    path_rule = (
        f"- 最终报告必须写入 `{report_path}`，并调用 present_report 展示该文件。\n"
        if report_path
        else "- 最终报告必须写入 Markdown 文件，并调用 present_report 展示该文件。\n"
    )
    headings = "\n".join(f"  - ## {heading}" for heading in REPORT_SECTION_HEADINGS)
    return (
        "最终报告格式与交付要求:\n"
        "- 报告必须是可直接用于下载、分享以及 PDF/Word 导出的中文 Markdown。\n"
        "- 使用一个 `#` 一级标题作为报告标题，随后按以下 `##` 二级标题顺序输出:\n"
        f"{headings}\n"
        "- 正文段落要完整成句；必要时使用 Markdown 表格承载对比信息。\n"
        "- 不要在最终报告中保留工具调用 JSON、过程日志、草稿占位符或未完成说明。\n"
        f"{path_rule}"
    )


def object_type_research_requirements(object_type: str) -> str:
    """Return the mandatory research lens for each supported business object type."""
    normalized = str(object_type or "").strip().upper()
    frameworks = {
        "COMPANY": (
            "对象类型专项调研框架（公司）:\n"
            "- 企业身份与资质: 工商信息、主营业务、管理层、股权或组织结构。\n"
            "- 经营与财务: 收入/利润/融资/经营数据，缺失时说明信息不可得。\n"
            "- 行业地位: 所处赛道、竞争格局、核心优势与短板。\n"
            "- 政策与监管: 相关政策、处罚、诉讼、合规或资质风险。\n"
            "- 近期动态: 新产品、合作、融资、舆情、重大公告或新闻。\n"
        ),
        "STOCK": (
            "对象类型专项调研框架（股票）:\n"
            "- 标的识别: 股票代码、交易市场、公司主体，避免同名混淆。\n"
            "- 行情与估值: 近期价格表现、成交/波动、估值水平或市场对比。\n"
            "- 财务与公告: 财报核心指标、盈利质量、现金流、重大公告。\n"
            "- 市场情绪: 机构观点、新闻舆情、行业景气度与资金关注点。\n"
            "- 投资风险: 监管风险、业绩不确定性、估值风险、流动性或宏观影响。\n"
        ),
        "PRODUCT": (
            "对象类型专项调研框架（商品）:\n"
            "- 商品定义: 品类、规格、目标用户、主要应用场景。\n"
            "- 价格与销量: 价格区间、走势、销量/渠道数据，缺失时标注来源局限。\n"
            "- 供需与竞争: 供应链、需求变化、竞品对比、替代品压力。\n"
            "- 用户反馈: 评价、投诉、口碑分化、核心购买/流失原因。\n"
            "- 外部影响: 成本、政策、季节性、渠道变化或质量安全风险。\n"
        ),
    }
    return frameworks.get(
        normalized,
        (
            "对象类型专项调研框架（通用商业对象）:\n"
            "- 明确对象身份与边界，避免同名对象混淆。\n"
            "- 覆盖对象概况、近期动态、市场位置、核心证据、风险与不确定性。\n"
        ),
    )
