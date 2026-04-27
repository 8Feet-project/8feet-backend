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


def citation_discipline_requirements(*, integrator: bool = False) -> str:
    """Return the citation discipline contract for final reports."""
    if integrator:
        source_rule = (
            "- 引用必须来自已读取的模型报告、evidence 文件或其中保留的原始 citation key；"
            "整合时保留原始 [@cite_key]，不要改写、合并或编造 citation key。\n"
            "- 如果某个模型报告缺少 citation key，只能把其中内容作为模型观点或待核验线索，"
            "不能写成已证实事实。\n"
        )
    else:
        source_rule = (
            "- 只有 web_fetch 或结构化业务数据工具返回的 citation key 才能支撑事实结论；"
            "web_search 只用于发现候选网址，不能把搜索摘要当作引用来源。\n"
        )
    return (
        "引用约束（句句有引用）:\n"
        "- 最终报告中的事实性断言、数字、时间、价格、财务指标、排名、对象状态、"
        "市场判断、竞争判断和风险判断，必须在同一句或同一表格单元格内带 [@cite_key]。\n"
        "- 一个句子包含多个独立事实时，引用键必须能够覆盖句内全部事实；"
        "无法被 citation key 支撑的确定性表达必须删除或改写为推断/待核验。\n"
        f"{source_rule}"
        "- 没有 citation key 的内容只能写入“风险与不确定性”，并明确标注为推断、假设或待核验。\n"
        "- 不要编造 citation key，不要引用未读取、未抓取或未出现在工具返回结果中的来源。\n"
    )


def search_then_research_workflow() -> str:
    """Return the recommended search -> research workflow for lead agents."""
    return (
        "DeepSearch 工作流建议:\n"
        "- 由 Lead Agent 根据任务复杂度判断调研深度、检索范围和是否需要子代理协作。\n"
        "- 先 search: 使用 web_search 或结构化业务数据工具识别信息面、候选来源、关键维度和明显争议点；"
        "web_search 结果已按 canonical URL 去重并附带 source_category/authority_score，选源时优先官方披露、监管机构、交易所、结构化数据和权威财经媒体。\n"
        "- 再 research: 对高价值候选来源使用 web_fetch 或结构化工具获取可引用证据，并围绕对象专项框架验证、归纳和证伪。\n"
        "- 证据入库时会尽量复用稳定 cite key：同一 canonical URL、同一复现代码，或同一小时内的实时数据快照，应视为同一证据来源。\n"
        "- 当任务内容较多、来源跨度大、需要多角度发散或需要剔除噪声时，可以调用 task 工具的 "
        "`deep-search` 子代理分角度搜索、筛选并沉淀 evidence 文件。\n"
        "- 当已有 evidence 但需要提出观点、比较证据、验证或证伪判断时，可以调用 task 工具的 "
        "`researcher` 子代理产出调研观点和论证摘要。\n"
        "- Lead Agent 始终负责最终取舍、整合和定稿；子代理只提供证据路径、概述、观点和不确定性。\n"
        "- 不要按来源数量机械停止；当证据已经覆盖对象专项框架、关键争议点和主要不确定性后，再收束生成最终报告。\n"
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
