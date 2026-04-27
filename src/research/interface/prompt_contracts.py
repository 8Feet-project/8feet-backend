"""Shared prompt contracts for research agents."""
from __future__ import annotations


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
