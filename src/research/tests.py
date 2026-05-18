import json
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from reports.interface.report_interface import get_report_detail
from reports.models.citation import Citation
from reports.models.report import Report
from research.interface import research_runtime
from research.interface.cross_validation import model_specs as cross_model_specs
from research.interface.cross_validation import orchestrator as cross_orchestrator
from research.interface.cross_validation import result_records as cross_result_records
from research.interface.cross_validation import task_records as cross_task_records
from research.interface.cross_validation.artifacts import (
    _is_llm_failure_output,
    _report_paths_from_payloads,
    _strip_tool_call_markup,
)
from research.interface.cross_validation.model_specs import _coerce_model_id_list
from research.interface.cross_validation.orchestrator import _integrator_candidates
from research.interface.cross_validation.payloads import _payload_from_result
from research.interface.cross_validation_runtime import (
    CrossModelSpec,
    build_cross_integrator_system_message,
    build_cross_integrator_prompt,
    build_cross_model_research_prompt,
    enqueue_cross_validation_run,
)
from research.interface.prompt_contracts import (
    citation_discipline_requirements,
    object_type_research_requirements,
    report_format_requirements,
    search_then_research_workflow,
)
from research.interface.research_runtime import (
    _build_execution_constraints,
    _record_event,
    _record_auto_step_approval,
    build_initial_prompt,
    build_research_system_message,
    build_research_system_message_for_user,
    _build_user_source_requirements,
    _resolve_max_turns,
)
from research.models import ResearchConversation, ResearchTask, ScrapedContent, TaskStepLog
from research.interface.research_interface import infer_object_type, respond_to_step
from research.api.research_api import (
    _agent_step_status,
    _attach_subagent_workflows,
    _coerce_bool,
    _collapse_agent_step_nodes,
    _dsml_tool_names,
    _dsml_report_tool_names,
    _enrich_report_links,
    _extract_subagent_workflows,
    _is_hidden_workflow_log,
    _pair_workflow_nodes,
    _progress_model,
    _split_report_message_nodes,
    _task_reference_items,
    _tool_display,
    _workflow_node_from_log,
)

class ResearchRuntimeConstraintTests(SimpleTestCase):
    def test_infer_object_type_keeps_explicit_selection(self):
        self.assertEqual(infer_object_type("腾讯控股", "stock"), "STOCK")
        self.assertEqual(infer_object_type("黄金", "company"), "COMPANY")

    def test_infer_object_type_handles_auto_detect_values(self):
        self.assertEqual(infer_object_type("600519", ""), "STOCK")
        self.assertEqual(infer_object_type("AAPL", "auto"), "STOCK")
        self.assertEqual(infer_object_type("黄金期货", "自动识别"), "PRODUCT")
        self.assertEqual(infer_object_type("腾讯控股", None), "COMPANY")

    def test_default_max_turns(self):
        self.assertEqual(_resolve_max_turns({}), 100)

    def test_custom_max_turns_in_search_params(self):
        self.assertEqual(_resolve_max_turns({"max_turns": 15}), 15)

    def test_max_turns_clamped_to_range(self):
        self.assertEqual(_resolve_max_turns({"max_turns": 2}), 6)
        self.assertEqual(_resolve_max_turns({"max_turns": 99}), 99)
        self.assertEqual(_resolve_max_turns({"max_turns": 120}), 100)

    def test_execution_constraints_do_not_limit_tool_call_counts(self):
        constraints = _build_execution_constraints({})

        self.assertIn("执行建议", constraints)
        self.assertIn("web_search 用于发现候选网址", constraints)
        self.assertIn("web_fetch 用于读取候选网页", constraints)
        self.assertIn("task 子代理:", constraints)
        self.assertIn("将调研拆解为多个子任务，优先通过 task 工具", constraints)
        self.assertIn("deep-search 并行发现证据", constraints)
        self.assertNotIn("最多调用", constraints)
        self.assertNotIn("task 子代理最多调用", constraints)
        self.assertNotIn("工具预算", constraints)
        self.assertNotIn("3 个以上可用来源", constraints)

    def test_subagents_can_be_disabled_by_task_params(self):
        constraints = _build_execution_constraints({"enable_subagents": False})

        self.assertNotIn("task 子代理:", constraints)

    def test_initial_prompt_includes_object_type_research_frameworks(self):
        cases = [
            ("COMPANY", "对象类型专项调研框架（公司）", "企业身份与资质", "政策与监管"),
            ("STOCK", "对象类型专项调研框架（股票）", "行情与估值", "财务与公告"),
            ("PRODUCT", "对象类型专项调研框架（商品）", "价格与销量", "用户反馈"),
        ]

        for object_type, title, first_dimension, second_dimension in cases:
            with self.subTest(object_type=object_type):
                task = SimpleNamespace(
                    title="专项调研",
                    object_name="Acme",
                    object_type=object_type,
                    search_params={},
                )

                prompt = build_initial_prompt(task)

                self.assertIn(title, prompt)
                self.assertIn(first_dimension, prompt)
                self.assertIn(second_dimension, prompt)

    def test_initial_prompt_includes_user_source_requirements(self):
        task = SimpleNamespace(
            title="带配置调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={
                "user_source_requirements": {
                    "time_range": "90d",
                    "source_authority": "authoritative",
                    "source_types": ["official", "data", "news"],
                    "research_focus": ["finance", "risk"],
                }
            },
        )

        prompt = build_initial_prompt(task)

        self.assertIn("用户配置要求", prompt)
        self.assertIn("时间范围: 近 90 天", prompt)
        self.assertIn("信息源类型: 官方披露、结构化数据、新闻舆情", prompt)
        self.assertIn("子项信息来源要求: 权威", prompt)
        self.assertIn("调研重点: 财务经营、风险合规", prompt)
        self.assertIn("把以上内容视为用户对检索和选源的要求", prompt)

    def test_user_source_requirements_supports_legacy_top_level_fields(self):
        text = _build_user_source_requirements(
            {
                "time_range": "30d",
                "source_authority": "unrestricted",
                "source_types": ["report"],
            }
        )

        self.assertIn("时间范围: 近 30 天", text)
        self.assertIn("子项信息来源要求: 无限制", text)
        self.assertIn("信息源类型: 研报分析", text)

    def test_object_type_contract_falls_back_to_generic_business_object(self):
        contract = object_type_research_requirements("UNKNOWN")

        self.assertIn("对象类型专项调研框架（通用商业对象）", contract)
        self.assertIn("避免同名对象混淆", contract)

    def test_report_format_contract_requires_export_ready_markdown(self):
        contract = report_format_requirements(
            "/mnt/user-data/outputs/research_report.md",
            "/mnt/user-data/outputs/research_report_brief.md",
        )

        self.assertIn("PDF/Word 导出", contract)
        self.assertIn("详细报告", contract)
        self.assertIn("简版报告", contract)
        self.assertIn("## 摘要", contract)
        self.assertIn("## 核心发现", contract)
        self.assertIn("## 关键证据", contract)
        self.assertIn("## 风险与不确定性", contract)
        self.assertIn("## 结论与建议", contract)
        self.assertIn("## 核心结论", contract)
        self.assertIn("## 关键依据", contract)
        self.assertIn("## 风险提示", contract)
        self.assertIn("/mnt/user-data/outputs/research_report.md", contract)
        self.assertIn("/mnt/user-data/outputs/research_report_brief.md", contract)
        self.assertIn("full_report_path", contract)
        self.assertIn("brief_report_path", contract)
        self.assertIn("present_report", contract)

    def test_search_then_research_workflow_guides_deepsearch_delegation(self):
        contract = search_then_research_workflow()

        self.assertIn("先拆解调研维度", contract)
        self.assertIn("deep-search 子代理并行检索", contract)
        self.assertIn("同一轮回复中一次性发出多个 task 工具调用", contract)
        self.assertIn("canonical URL 去重", contract)
        self.assertIn("authority_score", contract)
        self.assertIn("优先官方披露、监管机构、交易所", contract)
        self.assertIn("稳定 cite key", contract)
        self.assertIn("deep-search", contract)
        self.assertIn("researcher", contract)
        self.assertIn("web_fetch 或结构化工具", contract)

    def test_citation_contract_requires_fact_level_cite_keys(self):
        contract = citation_discipline_requirements()

        self.assertIn("引用约束（句句有引用）", contract)
        self.assertIn("事实性断言", contract)
        self.assertIn("必须在同一句或同一表格单元格内带 [@cite_key]", contract)
        self.assertIn("web_search 只用于发现候选网址", contract)
        self.assertIn("不要编造 citation key", contract)

    def test_research_system_message_and_initial_prompt_share_report_contract(self):
        system_message = build_research_system_message()
        task = SimpleNamespace(
            title="报告格式调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={},
        )
        prompt = build_initial_prompt(task)

        for text in (system_message, prompt):
            self.assertNotIn("先少量补充证据", text)
            self.assertNotIn("再尽快输出", text)
            self.assertNotIn("除非用户或任务参数明确要求 deep 深度模式", text)
            self.assertNotIn("3 个以上可用来源", text)
            self.assertIn("先拆解调研维度", text)
            self.assertIn("DeepSearch 工作流建议", text)
            self.assertIn("deep-search", text)
            self.assertIn("researcher", text)
            self.assertIn("最终报告格式与交付要求", text)
            self.assertIn("PDF/Word 导出", text)
            self.assertIn("详细报告", text)
            self.assertIn("简版报告", text)
            self.assertIn("## 摘要", text)
            self.assertIn("## 结论与建议", text)
            self.assertIn("brief_report_path", text)
            self.assertIn("present_report", text)

    def test_research_system_message_and_initial_prompt_share_citation_contract(self):
        system_message = build_research_system_message()
        task = SimpleNamespace(
            title="引用约束调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={},
        )
        prompt = build_initial_prompt(task)

        for text in (system_message, prompt):
            self.assertIn("引用约束（句句有引用）", text)
            self.assertIn("事实性断言", text)
            self.assertIn("[@cite_key]", text)
            self.assertIn("不要编造 citation key", text)

    def test_step_approval_prompt_is_lead_only(self):
        system_message = build_research_system_message()
        task = SimpleNamespace(
            title="审批调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={"auto_advance": False},
        )
        prompt = build_initial_prompt(task)

        for text in (system_message, prompt):
            self.assertIn("request_step_approval", text)
            self.assertIn("参数有且只有两个字符串", text)
            self.assertIn("仅适用于 Lead Agent", text)
            self.assertIn("不要在子代理 prompt 中提及该工具或审批规则", text)
        self.assertIn("当前任务关闭了自动推进", prompt)

    def test_research_system_message_includes_user_persona_when_present(self):
        user = SimpleNamespace(is_authenticated=True)

        with patch("research.interface.research_runtime.get_user_persona_markdown") as get_persona:
            get_persona.return_value = "# 用户调研人设分析\n\n偏好财务质量和风险提示。"
            system_message = build_research_system_message_for_user(user)

        self.assertIn("用户人设背景", system_message)
        self.assertIn("偏好财务质量和风险提示", system_message)

    def test_research_system_message_unchanged_without_persona(self):
        user = SimpleNamespace(is_authenticated=True)

        with patch("research.interface.research_runtime.get_user_persona_markdown", return_value=""):
            self.assertEqual(build_research_system_message_for_user(user), build_research_system_message())


class ResearchRealtimeReferenceTests(TestCase):
    def test_tool_result_event_persists_references_for_live_facts(self):
        user = get_user_model().objects.create_user(username="research-reference-user")
        task = ResearchTask.objects.create(
            user=user,
            title="实时参考信息",
            object_name="Caixin Global",
            object_type="COMPANY",
            status="SEARCHING",
        )
        conversation = ResearchConversation.objects.create(
            task=task,
            thread_id="thread-live-reference",
            state_snapshot={},
        )

        with patch.object(research_runtime, "publish_task_update") as publish_update:
            _record_event(
                task.id,
                1,
                {
                    "type": "tool_result",
                    "name": "web_fetch",
                    "id": "call_live_reference",
                    "content": "{}",
                    "citation_keys": ["web_fetch_caixinglobal_com_abc"],
                    "citations": [
                        {
                            "cite_key": "WEB_FETCH_CAIXINGLOBAL_COM_ABC",
                            "title": "Caixin Global article",
                            "url": "https://www.caixinglobal.com/article",
                            "source_platform": "caixinglobal.com",
                            "source_category": "news",
                            "summary": "article summary",
                        }
                    ],
                },
            )

        conversation.refresh_from_db()
        citations = conversation.state_snapshot["citations"]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["cite_key"], "web_fetch_caixinglobal_com_abc")
        self.assertTrue(
            ScrapedContent.objects.filter(
                task=task,
                source_url="https://www.caixinglobal.com/article",
                source_title="Caixin Global article",
            ).exists()
        )
        self.assertTrue(
            any(call.args[1] == "references_changed" for call in publish_update.call_args_list)
        )
        log = task.step_logs.get(step_name="工具返回: web_fetch")
        self.assertEqual(log.detail["citations"][0]["cite_key"], "WEB_FETCH_CAIXINGLOBAL_COM_ABC")

    def test_auto_step_approval_records_completed_interactive_log(self):
        user = get_user_model().objects.create_user(username="research-auto-approval-user")
        task = ResearchTask.objects.create(
            user=user,
            title="自动推进审批",
            object_name="Example",
            object_type="COMPANY",
            status="SEARCHING",
        )

        with patch.object(research_runtime, "publish_task_update") as publish_update:
            _record_auto_step_approval(
                task.id,
                1,
                {
                    "type": "tool_call",
                    "name": "request_step_approval",
                    "id": "call_approval",
                    "args": {
                        "next_action": "拆解调研维度",
                        "execution_plan": "先覆盖核心业务，再安排证据检索。",
                    },
                },
            )

        log = task.step_logs.get(step_name="拆解调研维度")
        self.assertEqual(log.step_status, "COMPLETED")
        self.assertTrue(log.is_interactive)
        self.assertTrue(log.detail["auto_accepted"])
        self.assertEqual(log.user_response["data"]["response"], "接受")
        self.assertTrue(any(call.args[1] == "step_log_created" for call in publish_update.call_args_list))

    def test_subagent_tool_result_content_guidance_persists_references_for_live_facts(self):
        user = get_user_model().objects.create_user(username="research-subagent-reference-user")
        task = ResearchTask.objects.create(
            user=user,
            title="子代理实时参考信息",
            object_name="Example",
            object_type="COMPANY",
            status="SEARCHING",
        )
        conversation = ResearchConversation.objects.create(
            task=task,
            thread_id="thread-live-subagent-reference",
            state_snapshot={},
        )

        content = {
            "ok": True,
            "url": "https://example.com/subagent-source",
            "title": "Subagent source",
            "citation_guidance": {
                "entries": [
                    {
                        "cite_key": "WEB_FETCH_EXAMPLE_SUBAGENT",
                        "source_platform": "example.com",
                        "source_category": "web",
                    }
                ],
            },
            "content": "# Subagent source\n\nEvidence summary.",
        }

        with patch.object(research_runtime, "publish_task_update") as publish_update:
            _record_event(
                task.id,
                1,
                {
                    "type": "subagent_tool_result",
                    "name": "web_fetch",
                    "id": "call_subagent_reference",
                    "content": json.dumps(content),
                    "citation_keys": ["web_fetch_example_subagent"],
                    "subagent_id": "subagent-live-reference",
                    "parent_tool_call_id": "call_task_reference",
                    "subagent_type": "deep-search",
                    "description": "采集子代理证据",
                },
            )

        conversation.refresh_from_db()
        citations = conversation.state_snapshot["citations"]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["cite_key"], "web_fetch_example_subagent")
        self.assertEqual(citations[0]["url"], "https://example.com/subagent-source")
        self.assertTrue(
            ScrapedContent.objects.filter(
                task=task,
                source_url="https://example.com/subagent-source",
                source_title="Subagent source",
            ).exists()
        )
        self.assertTrue(
            any(call.args[1] == "references_changed" for call in publish_update.call_args_list)
        )

    def test_success_recovery_backfills_report_citations_and_completes_present_report(self):
        user = get_user_model().objects.create_user(username="research-report-recovery-user")
        task = ResearchTask.objects.create(
            user=user,
            title="恢复调研报告",
            object_name="Example",
            object_type="COMPANY",
            status="ANALYZING",
            progress={},
        )
        conversation = ResearchConversation.objects.create(
            task=task,
            thread_id="thread-report-recovery",
            state_snapshot={},
        )
        report_markdown = "# 摘要\n\n结构化指标改善 [@akshare_key]，新闻披露支持判断 [@web_key]。"
        TaskStepLog.objects.create(
            task=task,
            step_name="工具返回: akshare_company_profile",
            step_status="COMPLETED",
            detail={
                "run_number": 1,
                "event_type": "subagent_tool_result",
                "id": "call_akshare",
                "content": json.dumps(
                    {
                        "ok": True,
                        "title": "AkShare company profile",
                        "source_platform": "akshare",
                        "provider": "akshare",
                        "content": "company profile data",
                        "citation_guidance": {
                            "entries": [
                                {
                                    "cite_key": "AKSHARE_KEY",
                                    "source_category": "financial",
                                    "howpublished": "akshare.company_profile",
                                }
                            ],
                        },
                    }
                ),
            },
        )
        TaskStepLog.objects.create(
            task=task,
            step_name="工具返回: web_fetch",
            step_status="COMPLETED",
            detail={
                "run_number": 1,
                "event_type": "tool_result",
                "id": "call_web",
                "content": "{}",
                "citations": [
                    {
                        "cite_key": "WEB_KEY",
                        "title": "Example news",
                        "url": "https://example.com/news",
                        "source_platform": "example.com",
                        "summary": "news summary",
                    }
                ],
            },
        )
        TaskStepLog.objects.create(
            task=task,
            step_name="调用工具: write_file",
            step_status="RUNNING",
            detail={
                "run_number": 1,
                "event_type": "tool_call",
                "id": "call_write",
                "args": {
                    "path": "/mnt/user-data/outputs/research_report.md",
                    "content": report_markdown,
                },
            },
        )
        present_call = TaskStepLog.objects.create(
            task=task,
            step_name="调用工具: present_report",
            step_status="RUNNING",
            detail={
                "run_number": 1,
                "event_type": "tool_call",
                "id": "call_present",
                "args": {"path": "/mnt/user-data/outputs/research_report.md"},
            },
        )

        with patch.object(research_runtime, "resolve_effective_output", return_value=research_runtime.STOPPED_MESSAGE):
            with patch.object(research_runtime, "log_model_usage"):
                research_runtime._persist_success(
                    task=task,
                    conversation=conversation,
                    thread=SimpleNamespace(history=[], state={}),
                    prompt="prompt",
                    final_output=research_runtime.STOPPED_MESSAGE,
                    create_report=True,
                    run_number=1,
                    previous_history_count=0,
                    previous_presented_report_count=0,
                    previous_report_row_count=0,
                    llm_config=None,
                    latency_ms=1.0,
                )

        report = Report.objects.get(task=task)
        self.assertEqual(report.content_markdown, report_markdown)
        self.assertEqual(Citation.objects.filter(report=report).count(), 2)

        conversation.refresh_from_db()
        recovered_keys = [item["cite_key"] for item in conversation.state_snapshot["citations"]]
        self.assertEqual(recovered_keys, ["akshare_key", "web_key"])

        present_call.refresh_from_db()
        self.assertEqual(present_call.step_status, "COMPLETED")
        self.assertEqual(present_call.detail["completed_by_event_type"], "report_persisted")
        self.assertEqual(present_call.detail["completed_by_report_id"], report.id)

        detail = get_report_detail(report.id)
        self.assertEqual([item["cite_key"] for item in detail["citations"]], ["akshare_key", "web_key"])
        self.assertIn("@misc{akshare_key", detail["references_bibtex"])
        self.assertIn("@misc{web_key", detail["references_bibtex"])

    def test_respond_to_step_requeues_approval_response(self):
        user = get_user_model().objects.create_user(username="research-approval-response-user")
        task = ResearchTask.objects.create(
            user=user,
            title="审批恢复",
            object_name="Example",
            object_type="COMPANY",
            status="WAITING_USER",
        )
        ResearchConversation.objects.create(task=task, thread_id="thread-approval-response")
        step = TaskStepLog.objects.create(
            task=task,
            step_name="拆解调研维度",
            step_status="PAUSED",
            is_interactive=True,
            detail={
                "event_type": "step_approval",
                "next_action": "拆解调研维度",
                "execution_plan": "先覆盖核心业务，再安排证据检索。",
            },
        )

        runtime = SimpleNamespace(
            _approval_response_prompt=lambda text: f"approval:{text}",
            enqueue_task_run=lambda *args, **kwargs: (True, None),
        )
        with patch("research.interface.research_interface._safe_research_runtime", return_value=(runtime, None)):
            with patch.object(runtime, "enqueue_task_run", return_value=(True, None)) as enqueue:
                success, message = respond_to_step(
                    task.id,
                    user.id,
                    step.id,
                    "reject",
                    {"comment": "证据不足"},
                )

        self.assertTrue(success)
        self.assertIsNone(message)
        step.refresh_from_db()
        self.assertEqual(step.step_status, "COMPLETED")
        self.assertEqual(step.user_response["data"]["response"], "拒绝，因为证据不足")
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["prompt"], "approval:拒绝，因为证据不足")
        self.assertEqual(enqueue.call_args.kwargs["queued_step_name"], "继续执行审批后的调研")

    def test_auto_advance_toggle_accepts_pending_step_approval(self):
        user = get_user_model().objects.create_user(
            username="research-auto-advance-api-user",
            is_superuser=True,
        )
        task = ResearchTask.objects.create(
            user=user,
            title="自动推进",
            object_name="Example",
            object_type="COMPANY",
            status="WAITING_USER",
            search_params={"auto_advance": False},
        )
        ResearchConversation.objects.create(task=task, thread_id="thread-auto-advance-api")
        step = TaskStepLog.objects.create(
            task=task,
            step_name="拆解调研维度",
            step_status="PAUSED",
            is_interactive=True,
            detail={
                "event_type": "step_approval",
                "next_action": "拆解调研维度",
                "execution_plan": "先覆盖核心业务，再安排证据检索。",
            },
        )
        token = jwt.encode(
            {"user_id": user.id, "type": "access_token"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
        runtime = SimpleNamespace(
            _approval_response_prompt=lambda text: f"approval:{text}",
            enqueue_task_run=lambda *args, **kwargs: (True, None),
        )

        with patch("research.interface.research_interface._safe_research_runtime", return_value=(runtime, None)):
            with patch.object(runtime, "enqueue_task_run", return_value=(True, None)) as enqueue:
                response = client.post(
                    f"/api/v1/research/tasks/{task.id}/auto-advance",
                    data=json.dumps({"auto_advance": True}),
                    content_type="application/json",
                )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["code"], 0)
        self.assertTrue(payload["data"]["auto_advance"])
        self.assertTrue(payload["data"]["resumed"])
        step.refresh_from_db()
        self.assertEqual(step.step_status, "COMPLETED")
        self.assertTrue(step.detail["auto_accepted"])
        self.assertEqual(step.user_response["data"]["response"], "接受")
        enqueue.assert_called_once()


class CrossValidationRuntimeTests(SimpleTestCase):
    def test_model_id_list_accepts_json_array_and_comma_text(self):
        self.assertEqual(_coerce_model_id_list('["a", "b"]'), ["a", "b"])
        self.assertEqual(_coerce_model_id_list("a,b, c "), ["a", "b", "c"])
        self.assertEqual(_coerce_model_id_list({"models": ["m1", "m2"]}), ["m1", "m2"])

    def test_cross_model_prompt_requires_independent_presented_report(self):
        task = SimpleNamespace(title="T", object_name="Acme", object_type="COMPANY")
        prompt = build_cross_model_research_prompt(task, "base task")

        self.assertIn("独立调研线程", prompt)
        self.assertIn("对象类型专项调研框架（公司）", prompt)
        self.assertIn("企业身份与资质", prompt)
        self.assertIn("/mnt/user-data/outputs/model_research_report.md", prompt)
        self.assertIn("/mnt/user-data/outputs/model_research_report_brief.md", prompt)
        self.assertIn("最终报告格式与交付要求", prompt)
        self.assertIn("## 摘要", prompt)
        self.assertIn("## 核心结论", prompt)
        self.assertIn("## 结论与建议", prompt)
        self.assertIn("引用约束（句句有引用）", prompt)
        self.assertIn("web_search 只用于发现候选网址", prompt)
        self.assertIn("brief_report_path", prompt)
        self.assertIn("present_report", prompt)


class ResearchProgressModelTests(SimpleTestCase):
    def test_progress_model_uses_fixed_business_stage_template(self):
        task = SimpleNamespace(
            status="WAITING_USER",
            progress={
                "stage": "ANALYZING",
                "searching": 100,
                "analyzing": 82,
                "report": 0,
            },
        )

        model = _progress_model(task)

        self.assertEqual(model["total_weight"], 100)
        self.assertEqual(len(model["stages"]), 4)
        self.assertEqual(
            [stage["key"] for stage in model["stages"]],
            ["ingest", "retrieval", "analysis", "report"],
        )
        self.assertEqual(model["stages"][0]["status"], "completed")
        self.assertEqual(model["stages"][1]["status"], "completed")
        self.assertEqual(model["stages"][2]["status"], "waiting_user")
        self.assertEqual(model["stages"][3]["status"], "pending")
        self.assertEqual(model["stages"][2]["progress_percent"], 85)
        self.assertEqual(model["percent"], 75)

    def test_workflow_hides_shell_step_logs(self):
        self.assertTrue(
            _is_hidden_workflow_log(
                {
                    "step_name": "开始执行调研",
                    "detail": {"event_type": "message"},
                }
            )
        )
        self.assertTrue(
            _is_hidden_workflow_log(
                {
                    "step_name": "调研完成",
                    "detail": {
                        "event_type": "message",
                        "message": "<｜DSML｜tool_calls><｜DSML｜invoke name=\"write_file\"></｜DSML｜invoke>",
                    },
                }
            )
        )

        self.assertFalse(
            _is_hidden_workflow_log(
                {
                    "step_name": "生成回答",
                    "detail": {"event_type": "message", "message": "报告已生成。"},
                }
            )
        )
        self.assertFalse(
            _is_hidden_workflow_log(
                {
                    "step_name": "调研完成",
                    "detail": {"message": "报告已生成并展示。"},
                }
            )
        )

        self.assertFalse(
            _is_hidden_workflow_log(
                {
                    "step_name": "规划下一步",
                    "detail": {"event_type": "pre_tool_text"},
                }
            )
        )

    def test_planning_nodes_do_not_remain_running(self):
        node = _workflow_node_from_log(
            {
                "id": 11,
                "step_name": "规划下一步",
                "step_status": "RUNNING",
                "detail": {
                    "event_type": "pre_tool_text",
                    "message": "搜索近期公告和新闻。",
                },
            },
            0,
        )

        self.assertEqual(node["node_kind"], "planning")
        self.assertEqual(node["node_status"], "completed")

    def test_message_nodes_use_reply_text_and_do_not_remain_running(self):
        node = _workflow_node_from_log(
            {
                "id": 12,
                "step_name": "生成回答",
                "step_status": "RUNNING",
                "detail": {
                    "event_type": "message",
                    "message": "报告已生成并展示，可进入报告页查看。",
                },
            },
            0,
        )

        self.assertEqual(node["node_kind"], "llm_message")
        self.assertEqual(node["node_status"], "completed")
        self.assertEqual(node["node_name"], "报告已生成并展示，可进入报告页查看。")
        self.assertEqual(node["summary"], "报告已生成并展示，可进入报告页查看。")

    def test_agent_step_status_ignores_planning_status(self):
        status = _agent_step_status(
            [
                {"node_kind": "planning", "node_status": "running"},
                {"node_kind": "tool_call", "node_status": "completed"},
                {"node_kind": "tool_return", "node_status": "completed"},
            ]
        )

        self.assertEqual(status, "completed")

    def test_step_approval_log_becomes_waiting_human_review_node(self):
        node = _workflow_node_from_log(
            {
                "id": 13,
                "step_name": "拆解调研维度",
                "step_status": "PAUSED",
                "is_interactive": True,
                "detail": {
                    "event_type": "step_approval",
                    "next_action": "拆解调研维度",
                    "execution_plan": "先覆盖核心业务，再安排证据检索。",
                    "message": "拆解调研维度\n先覆盖核心业务，再安排证据检索。",
                    "approval_options": ["accept", "replan", "reject"],
                },
            },
            0,
        )

        self.assertEqual(node["node_kind"], "human_review")
        self.assertEqual(node["node_status"], "waiting_user")
        self.assertTrue(node["can_intervene"])
        self.assertEqual(node["node_name"], "拆解调研维度")
        self.assertEqual(node["summary"], "先覆盖核心业务，再安排证据检索。")
        self.assertEqual(node["payload"]["next_action"], "拆解调研维度")
        self.assertEqual(node["payload"]["approval_options"], ["accept", "replan", "reject"])

    def test_coerce_bool_accepts_toggle_payload_values(self):
        self.assertTrue(_coerce_bool(True))
        self.assertTrue(_coerce_bool("true"))
        self.assertFalse(_coerce_bool(False))
        self.assertFalse(_coerce_bool("false"))

    def test_dsml_report_message_becomes_light_agent_step(self):
        message = (
            "我要生成调研报告。\n\n"
            "<｜DSML｜tool_calls>\n"
            "<｜DSML｜invoke name=\"write_file\">"
            "<｜DSML｜parameter name=\"path\" string=\"true\">/mnt/user-data/outputs/research_report.md</｜DSML｜parameter>"
            "</｜DSML｜invoke>\n"
            "</｜DSML｜tool_calls>"
        )
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 21,
                    "step_name": "生成回答",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "message", "message": message},
                },
                0,
            )
        ]

        collapsed = _collapse_agent_step_nodes(raw_nodes)
        expanded = _split_report_message_nodes(
            collapsed,
            {"report_id": "5", "report_title": "报告", "report_url": "/report?task_id=6&report_id=5"},
        )

        self.assertEqual(len(expanded), 1)
        node = expanded[0]
        self.assertEqual(node["node_kind"], "agent_step")
        self.assertEqual(node["node_status"], "completed")
        self.assertEqual(node["summary"], "我要生成调研报告。")
        self.assertEqual(node["payload"]["report_url"], "/report?task_id=6&report_id=5")
        self.assertEqual(node["payload"]["tools"][0]["tool_name"], "write_file")
        self.assertFalse(node["payload"]["tools"][0]["hide_payload"])
        self.assertEqual(node["payload"]["tools"][0]["report_url"], "")

    def test_report_generation_and_summary_remain_separate_nodes(self):
        write_message = (
            "我要生成调研报告。"
            "<｜DSML｜tool_calls><｜DSML｜invoke name=\"write_file\"></｜DSML｜invoke></｜DSML｜tool_calls>"
        )
        present_message = (
            "我要展示调研报告。"
            "<｜DSML｜tool_calls><｜DSML｜invoke name=\"present_report\"></｜DSML｜invoke></｜DSML｜tool_calls>"
        )
        summary_message = "报告已生成并展示，可进入报告页查看。"
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 31,
                    "step_name": "生成回答",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "message", "message": write_message},
                },
                0,
            ),
            _workflow_node_from_log(
                {
                    "id": 32,
                    "step_name": "生成回答",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "message", "message": present_message},
                },
                1,
            ),
            _workflow_node_from_log(
                {
                    "id": 33,
                    "step_name": "生成回答",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "message", "message": summary_message},
                },
                2,
            ),
        ]

        nodes = _split_report_message_nodes(
            _collapse_agent_step_nodes(raw_nodes),
            {"report_id": "8", "report_title": "报告", "report_url": "/report?task_id=9&report_id=8"},
        )
        _enrich_report_links(nodes, {"report_id": "8", "report_title": "报告", "report_url": "/report?task_id=9&report_id=8"})

        self.assertEqual([node["node_kind"] for node in nodes], ["agent_step", "agent_step", "llm_message"])
        self.assertEqual([node["summary"] for node in nodes], ["我要生成调研报告。", "我要展示调研报告。", summary_message])
        self.assertEqual(nodes[0]["payload"]["tools"][0]["tool_name"], "write_file")
        self.assertEqual(nodes[1]["payload"]["tools"][0]["tool_name"], "present_report")
        self.assertFalse(nodes[0]["payload"]["tools"][0]["hide_payload"])
        self.assertTrue(nodes[1]["payload"]["tools"][0]["hide_payload"])
        self.assertEqual(nodes[1]["payload"]["report_url"], "/report?task_id=9&report_id=8")
        self.assertNotIn("tools", nodes[2]["payload"])

    def test_subagent_report_and_file_events_are_nested_under_task_tool(self):
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 41,
                    "step_name": "调用工具: task",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "tool_call",
                        "id": "call_task_1",
                        "args": {"description": "委托子代理"},
                    },
                },
                0,
            ),
            _workflow_node_from_log(
                {
                    "id": 42,
                    "step_name": "[subagent-1] 启动子代理",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "subagent_start",
                        "description": "委托子代理",
                        "subagent_id": "subagent-1",
                        "parent_tool_call_id": "call_task_1",
                        "subagent_type": "researcher",
                    },
                },
                1,
            ),
            _workflow_node_from_log(
                {
                    "id": 43,
                    "step_name": "[subagent-1] 展示文件",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "files_presented",
                        "paths": ["/mnt/user-data/outputs/subagent.txt"],
                        "subagent_id": "subagent-1",
                        "parent_tool_call_id": "call_task_1",
                        "subagent_type": "researcher",
                    },
                },
                2,
            ),
            _workflow_node_from_log(
                {
                    "id": 44,
                    "step_name": "[subagent-1] 产出报告",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "report_presented",
                        "path": "/mnt/user-data/outputs/subagent.md",
                        "brief_path": "/mnt/user-data/outputs/subagent_brief.md",
                        "content_length": 12,
                        "brief_content_length": 6,
                        "subagent_id": "subagent-1",
                        "parent_tool_call_id": "call_task_1",
                        "subagent_type": "researcher",
                    },
                },
                3,
            ),
            _workflow_node_from_log(
                {
                    "id": 45,
                    "step_name": "[subagent-1] 子代理完成",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "subagent_complete",
                        "message": "完成",
                        "subagent_id": "subagent-1",
                        "parent_tool_call_id": "call_task_1",
                        "subagent_type": "researcher",
                    },
                },
                4,
            ),
            _workflow_node_from_log(
                {
                    "id": 46,
                    "step_name": "工具返回: task",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_task_1",
                        "content": "Task succeeded. Result: 完成",
                    },
                },
                5,
            ),
        ]

        subagent_workflows = _extract_subagent_workflows(raw_nodes)
        _attach_subagent_workflows(raw_nodes, subagent_workflows)
        raw_nodes = [node for node in raw_nodes if not node.get("_is_subagent_node")]
        _pair_workflow_nodes(raw_nodes)
        nodes = _collapse_agent_step_nodes(raw_nodes)

        self.assertEqual(len(nodes), 1)
        tools = nodes[0]["payload"]["tools"]
        self.assertEqual(tools[0]["tool_name"], "task")
        workflows = tools[0]["subagent_workflows"]
        self.assertEqual(len(workflows), 1)
        self.assertEqual(workflows[0]["subagent_id"], "subagent-1")
        self.assertEqual(
            [node["event_type"] for node in workflows[0]["nodes"]],
            ["files_presented", "report_presented"],
        )

    def test_tool_batches_after_task_result_become_separate_agent_steps(self):
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 51,
                    "step_name": "调用工具: task",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "tool_call",
                        "id": "call_task_1",
                        "tool_call_batch_id": "lead:batch-task",
                    },
                },
                0,
            ),
            _workflow_node_from_log(
                {
                    "id": 52,
                    "step_name": "调用工具: task",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "tool_call",
                        "id": "call_task_2",
                        "tool_call_batch_id": "lead:batch-task",
                    },
                },
                1,
            ),
            _workflow_node_from_log(
                {
                    "id": 53,
                    "step_name": "工具返回: task",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_task_1",
                        "content": "Task succeeded. Result: A",
                        "tool_call_batch_id": "lead:batch-task",
                    },
                },
                2,
            ),
            _workflow_node_from_log(
                {
                    "id": 54,
                    "step_name": "工具返回: task",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_task_2",
                        "content": "Task succeeded. Result: B",
                        "tool_call_batch_id": "lead:batch-task",
                    },
                },
                3,
            ),
            _workflow_node_from_log(
                {
                    "id": 55,
                    "step_name": "调用工具: akshare_market_data",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "tool_call",
                        "id": "call_market_1",
                        "tool_call_batch_id": "lead:batch-market",
                    },
                },
                4,
            ),
            _workflow_node_from_log(
                {
                    "id": 56,
                    "step_name": "工具返回: akshare_market_data",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_market_1",
                        "content": "{}",
                        "tool_call_batch_id": "lead:batch-market",
                    },
                },
                5,
            ),
        ]

        _pair_workflow_nodes(raw_nodes)
        nodes = _collapse_agent_step_nodes(raw_nodes)

        self.assertEqual(len(nodes), 2)
        self.assertEqual(
            [tool["tool_name"] for tool in nodes[0]["payload"]["tools"]],
            ["task", "task"],
        )
        self.assertEqual(
            [tool["tool_name"] for tool in nodes[1]["payload"]["tools"]],
            ["akshare_market_data"],
        )
        self.assertEqual(nodes[0]["payload"]["source_node_ids"], ["51", "52", "53", "54"])
        self.assertEqual(nodes[1]["payload"]["source_node_ids"], ["55", "56"])

    def test_legacy_tool_batches_split_when_new_call_follows_returns(self):
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 61,
                    "step_name": "调用工具: task",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "tool_call", "id": "call_task_1"},
                },
                0,
            ),
            _workflow_node_from_log(
                {
                    "id": 62,
                    "step_name": "工具返回: task",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_task_1",
                        "content": "Task succeeded. Result: A",
                    },
                },
                1,
            ),
            _workflow_node_from_log(
                {
                    "id": 63,
                    "step_name": "调用工具: akshare_market_data",
                    "step_status": "RUNNING",
                    "detail": {"event_type": "tool_call", "id": "call_market_1"},
                },
                2,
            ),
            _workflow_node_from_log(
                {
                    "id": 64,
                    "step_name": "工具返回: akshare_market_data",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_market_1",
                        "content": "{}",
                    },
                },
                3,
            ),
        ]

        _pair_workflow_nodes(raw_nodes)
        nodes = _collapse_agent_step_nodes(raw_nodes)

        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["payload"]["tools"][0]["tool_name"], "task")
        self.assertEqual(nodes[1]["payload"]["tools"][0]["tool_name"], "akshare_market_data")

    def test_report_presented_event_is_grouped_with_present_report_tool(self):
        raw_nodes = [
            _workflow_node_from_log(
                {
                    "id": 71,
                    "step_name": "调用工具: present_report",
                    "step_status": "RUNNING",
                    "detail": {
                        "event_type": "tool_call",
                        "id": "call_present",
                        "tool_call_batch_id": "lead:batch-report",
                    },
                },
                0,
            ),
            _workflow_node_from_log(
                {
                    "id": 72,
                    "step_name": "产出报告",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "report_presented",
                        "path": "/mnt/user-data/outputs/research_report.md",
                        "tool_call_batch_id": "lead:batch-report",
                    },
                },
                1,
            ),
            _workflow_node_from_log(
                {
                    "id": 73,
                    "step_name": "工具返回: present_report",
                    "step_status": "COMPLETED",
                    "detail": {
                        "event_type": "tool_result",
                        "id": "call_present",
                        "content": "Successfully presented report.",
                        "tool_call_batch_id": "lead:batch-report",
                    },
                },
                2,
            ),
        ]

        _pair_workflow_nodes(raw_nodes)
        nodes = _collapse_agent_step_nodes(raw_nodes)

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_kind"], "agent_step")
        self.assertEqual(nodes[0]["payload"]["tools"][0]["tool_name"], "present_report")
        self.assertEqual(nodes[0]["payload"]["source_node_ids"], ["71", "72", "73"])

    def test_dsml_tool_names_extracts_multiple_calls(self):
        names = _dsml_tool_names(
            "<｜DSML｜tool_calls>"
            "<｜DSML｜invoke name=\"write_file\"></｜DSML｜invoke>"
            "<｜DSML｜invoke name=\"present_report\"></｜DSML｜invoke>"
            "</｜DSML｜tool_calls>"
        )

        self.assertEqual(names, ["write_file", "present_report"])

    def test_dsml_report_tool_names_requires_report_context_for_write_file(self):
        self.assertEqual(
            _dsml_report_tool_names(
                "<｜DSML｜tool_calls>"
                "<｜DSML｜invoke name=\"write_file\">"
                "/mnt/user-data/outputs/research_report.md"
                "</｜DSML｜invoke>"
            ),
            [],
        )
        self.assertEqual(
            _dsml_report_tool_names(
                "<｜DSML｜tool_calls>"
                "<｜DSML｜invoke name=\"present_report\">"
                "/mnt/user-data/outputs/research_report.md"
                "</｜DSML｜invoke>"
            ),
            ["present_report"],
        )

    def test_task_reference_items_uses_state_citations(self):
        task = SimpleNamespace(
            id=7,
            conversation=SimpleNamespace(
                state_snapshot={
                    "citations": [
                        {
                            "cite_key": "web_fetch_example",
                            "title": "示例来源",
                            "url": "https://example.com/source",
                            "source_platform": "example.com",
                            "source_category": "web",
                            "authority_score": 72,
                            "summary": "可引用证据摘要",
                            "accessed_at": "2026-05-10T12:00:00",
                        }
                    ]
                }
            ),
        )

        references = _task_reference_items(task)

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["cite_key"], "web_fetch_example")
        self.assertEqual(references[0]["title"], "示例来源")
        self.assertEqual(
            references[0]["evidence_path"],
            "/mnt/user-data/workspace/evidence/web_fetch_example.md",
        )

    def test_tool_display_localizes_web_search_status(self):
        display_name, status_text = _tool_display(
            "web_search",
            "completed",
            {"query": "泡泡玛特 财报"},
            {"total_results": 8},
        )

        self.assertEqual(display_name, "网页搜索")
        self.assertEqual(status_text, "已搜索到 8 条信息")

    def test_integrator_system_message_requires_report_contract(self):
        system_message = build_cross_integrator_system_message()

        self.assertIn("最终报告格式与交付要求", system_message)
        self.assertIn("引用约束（句句有引用）", system_message)
        self.assertIn("保留原始 [@cite_key]", system_message)
        self.assertIn("不要改写、合并或编造 citation key", system_message)
        self.assertIn("PDF/Word 导出", system_message)
        self.assertIn("## 核心发现", system_message)
        self.assertIn("/mnt/user-data/outputs/cross_validation_report.md", system_message)
        self.assertIn("/mnt/user-data/outputs/cross_validation_report_brief.md", system_message)
        self.assertIn("brief_report_path", system_message)
        self.assertIn("present_report", system_message)

    def test_integrator_prompt_points_to_copied_reports(self):
        prompt = build_cross_integrator_prompt(
            {"title": "T", "object_name": "Acme", "object_type": "COMPANY"},
            [
                {
                    "status": "completed",
                    "model": {"model_name": "m1"},
                    "thread_id": "thread-1",
                    "summary": "summary",
                    "report_paths": ["/mnt/user-data/outputs/report.md"],
                }
            ],
            [
                {
                    "model_name": "m1",
                    "directory": "/mnt/user-data/workspace/cross_validation_inputs/m1",
                    "report_paths": [
                        {
                            "source_path": "/mnt/user-data/outputs/report.md",
                            "copied_path": "/mnt/user-data/workspace/cross_validation_inputs/m1/outputs/report.md",
                        }
                    ],
                }
            ],
        )

        self.assertIn("copied_path", prompt)
        self.assertIn("对象类型专项核查框架", prompt)
        self.assertIn("企业身份与资质", prompt)
        self.assertIn("cross_validation_report.md", prompt)
        self.assertIn("cross_validation_report_brief.md", prompt)
        self.assertIn("最终报告格式与交付要求", prompt)
        self.assertIn("## 风险与不确定性", prompt)
        self.assertIn("## 风险提示", prompt)
        self.assertIn("引用约束（句句有引用）", prompt)
        self.assertIn("不能写成已证实事实", prompt)
        self.assertIn("智能整合优化", prompt)

    def test_presented_report_event_persists_full_and_brief_variants(self):
        task = SimpleNamespace(id=1)
        event = {
            "type": "report_presented",
            "path": "/mnt/user-data/outputs/report.md",
            "full_path": "/mnt/user-data/outputs/report.md",
            "brief_path": "/mnt/user-data/outputs/report_brief.md",
            "content": "# 报告\n\n详细内容。",
            "brief_content": "# 报告\n\n简要内容。",
            "citations": [{"url": "https://example.com", "title": "Example"}],
        }

        with patch.object(research_runtime, "_create_report", return_value=SimpleNamespace(id=9)) as create_report:
            result = research_runtime._persist_presented_report_event(task, event)

        self.assertEqual(result.id, 9)
        self.assertIs(create_report.call_args.args[0], task)
        self.assertEqual(create_report.call_args.args[1], "# 报告\n\n详细内容。")
        self.assertEqual(create_report.call_args.kwargs["brief_output"], "# 报告\n\n简要内容。")

    def test_payload_from_result_exposes_consensus_difference_and_reports(self):
        now = timezone.now()
        task = SimpleNamespace(id=7, updated_at=now)
        result = SimpleNamespace(
            id=9,
            created_at=now,
            conclusion="summary",
            raw_output={
                "cross_validation_run_id": "run-1",
                "model_outputs": [
                    {
                        "status": "completed",
                        "model": {"model_name": "m1"},
                        "thread_id": "thread-1",
                        "summary": "m1 summary",
                        "report_paths": ["/mnt/user-data/outputs/report.md"],
                        "presented_reports": [{"path": "/mnt/user-data/outputs/report.md"}],
                    }
                ],
                "used_models": [{"model_name": "m1"}],
                "integrator": {
                    "final_output": "# 多模型共识\n- 共同结论\n\n# 主要分歧\n- 分歧点",
                    "report_paths": [
                        "/mnt/user-data/outputs/cross_validation_report.md",
                        "/mnt/user-data/outputs/cross_validation_report_brief.md",
                    ],
                    "presented_reports": [
                        {
                            "full_path": "/mnt/user-data/outputs/cross_validation_report.md",
                            "brief_path": "/mnt/user-data/outputs/cross_validation_report_brief.md",
                        }
                    ],
                },
            },
        )

        payload = _payload_from_result(task, result)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["consensus_points"], ["共同结论"])
        self.assertEqual(payload["difference_points"], ["分歧点"])
        self.assertEqual(payload["report_path"], "/mnt/user-data/outputs/cross_validation_report.md")
        self.assertEqual(payload["used_models"], ["m1"])
        self.assertEqual(payload["model_outputs"][0]["model_id"], "m1")

    def test_report_paths_from_payloads_returns_full_then_brief_paths(self):
        paths = _report_paths_from_payloads(
            [
                {
                    "path": "/mnt/user-data/outputs/report.md",
                    "full_path": "/mnt/user-data/outputs/report.md",
                    "brief_path": "/mnt/user-data/outputs/report_brief.md",
                }
            ]
        )

        self.assertEqual(
            paths,
            ["/mnt/user-data/outputs/report.md", "/mnt/user-data/outputs/report_brief.md"],
        )

    def test_provider_failure_text_is_not_treated_as_report(self):
        self.assertTrue(
            _is_llm_failure_output(
                "The configured LLM provider is temporarily unavailable after multiple retries."
            )
        )
        self.assertTrue(
            research_runtime._is_llm_failure_output(
                "The configured LLM provider is temporarily unavailable after multiple retries."
            )
        )
        self.assertTrue(
            research_runtime._is_llm_failure_output(
                "The configured LLM provider rate limit was exceeded after multiple retries."
            )
        )
        self.assertFalse(_is_llm_failure_output("# 正常报告\n- 结论"))

    def test_provider_failure_output_aborts_success_persistence(self):
        task = SimpleNamespace(id=1)
        conversation = SimpleNamespace()
        thread = SimpleNamespace(history=[], state={})
        failure_text = "The configured LLM provider is temporarily unavailable after multiple retries."

        with patch.object(research_runtime, "resolve_effective_output", return_value=failure_text):
            with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
                research_runtime._persist_success(
                    task=task,
                    conversation=conversation,
                    thread=thread,
                    prompt="prompt",
                    final_output=failure_text,
                    create_report=True,
                    run_number=1,
                    previous_history_count=0,
                    previous_presented_report_count=0,
                    previous_report_row_count=0,
                    llm_config=None,
                    latency_ms=1.0,
                )

    def test_tool_call_markup_is_stripped_from_report_tail(self):
        self.assertEqual(
            _strip_tool_call_markup("# 报告\n正文\n<longcat_tool_call>{...}"),
            "# 报告\n正文",
        )

    def test_integrator_candidates_fall_back_to_successful_models(self):
        requested = CrossModelSpec("a", "model-a", "env", {"model": "a", "api_key": "k", "base_url": "u"})
        fallback = CrossModelSpec("b", "model-b", "env", {"model": "b", "api_key": "k", "base_url": "u"})

        candidates = _integrator_candidates(
            requested,
            [requested, fallback],
            [{"model": {"model_name": "model-b"}}],
        )

        self.assertEqual([item.model_name for item in candidates], ["model-a", "model-b"])

    def test_env_model_names_are_rejected_unless_debug_flag_is_explicit(self):
        task = SimpleNamespace(user=SimpleNamespace(id=3), object_type="COMPANY")

        class EmptyConfigManager:
            def filter(self, *args, **kwargs):
                return self

            def order_by(self, *args):
                return self

            def first(self):
                return None

        with patch.object(cross_model_specs.LLMConfig, "objects", EmptyConfigManager()):
            with patch.dict(os.environ, {"MODEL_API_KEY": "k", "MODEL_BASE_URL": "u"}):
                with self.assertRaisesRegex(ValueError, "未在平台配置"):
                    cross_model_specs._resolve_model_spec(task, "deepseek-v4-flash")

                spec = cross_model_specs._resolve_model_spec(
                    task,
                    "deepseek-v4-flash",
                    allow_env_models=True,
                )

        self.assertEqual(spec.model_name, "deepseek-v4-flash")
        self.assertIsNone(spec.llm_config_id)

    def test_model_child_success_persists_report_on_child_task(self):
        child_task = SimpleNamespace(
            id=21,
            user=SimpleNamespace(id=3),
            title="Acme - model-a 交叉验证",
            progress={},
        )
        spec = CrossModelSpec(
            "model-a",
            "model-a",
            "provider-a",
            {"model": "model-a", "api_key": "k", "base_url": "u"},
            llm_config_id=5,
            order=1,
        )
        analysis_rows = []
        task_updates = []
        step_rows = []

        class FakeTaskManager:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return self

            def first(self):
                return child_task

            def update(self, **kwargs):
                task_updates.append(kwargs)
                return 1

        class FakeAnalysisManager:
            def create(self, **kwargs):
                analysis_rows.append(kwargs)
                return SimpleNamespace(id=31)

        class FakeConversationManager:
            def update_or_create(self, **kwargs):
                return (SimpleNamespace(id=41), True)

        class FakeStepManager:
            def create(self, **kwargs):
                step_rows.append(kwargs)
                return SimpleNamespace(**kwargs)

        with patch.object(cross_task_records.ResearchTask, "objects", FakeTaskManager()):
            with patch.object(cross_task_records.AnalysisResult, "objects", FakeAnalysisManager()):
                with patch.object(cross_task_records.ResearchConversation, "objects", FakeConversationManager()):
                    with patch.object(cross_task_records.TaskStepLog, "objects", FakeStepManager()):
                        with patch.object(cross_task_records.transaction, "atomic", return_value=nullcontext()):
                            with patch.object(cross_task_records.research_runtime, "_extract_citations", return_value=[]):
                                with patch.object(
                                    cross_task_records.research_runtime,
                                    "_create_report",
                                    return_value=SimpleNamespace(id=51),
                                ) as create_report:
                                    with patch.object(cross_task_records, "log_model_usage") as log_usage:
                                        persisted = cross_task_records._persist_model_child_success(
                                            child_task_id=child_task.id,
                                            run_id="run-1",
                                            spec=spec,
                                            thread_id="thread-1",
                                            prompt="prompt",
                                            final_output="# Report",
                                            serialized_history=[],
                                            state_snapshot={},
                                            presented_reports=[{"path": "/mnt/user-data/outputs/report.md"}],
                                            latency_ms=12.3,
                                        )

        self.assertIs(create_report.call_args.args[0], child_task)
        self.assertEqual(analysis_rows[0]["task"], child_task)
        self.assertEqual(persisted["child_report_id"], 51)
        self.assertEqual(task_updates[0]["status"], "COMPLETED")
        self.assertEqual(step_rows[0]["task"], child_task)
        log_usage.assert_called_once()

    def test_cross_success_persists_integrated_report_on_parent_task(self):
        parent_task = SimpleNamespace(
            id=7,
            user=SimpleNamespace(id=3),
            title="Acme 深度调研",
        )
        integrator_spec = CrossModelSpec(
            "integrator",
            "integrator",
            "provider-a",
            {"model": "integrator", "api_key": "k", "base_url": "u"},
            llm_config_id=9,
        )
        analysis_rows = []
        step_rows = []

        class FakeAnalysisManager:
            def create(self, **kwargs):
                analysis_rows.append(kwargs)
                return SimpleNamespace(id=61)

        class FakeStepManager:
            def create(self, **kwargs):
                step_rows.append(kwargs)
                return SimpleNamespace(**kwargs)

        with patch.object(cross_result_records.AnalysisResult, "objects", FakeAnalysisManager()):
            with patch.object(cross_result_records.TaskStepLog, "objects", FakeStepManager()):
                with patch.object(cross_result_records.transaction, "atomic", return_value=nullcontext()):
                    with patch.object(cross_result_records.research_runtime, "_extract_citations", return_value=[]):
                        with patch.object(
                            cross_result_records.research_runtime,
                            "_create_report",
                            return_value=SimpleNamespace(id=71),
                        ) as create_report:
                            with patch.object(cross_result_records, "_update_cross_log"):
                                with patch.object(cross_result_records, "_update_cross_progress"):
                                    with patch.object(cross_result_records, "log_model_usage"):
                                        cross_result_records._persist_cross_success(
                                            task=parent_task,
                                            run_id="run-1",
                                            prompt="prompt",
                                            model_results=[{"child_task_id": 21, "status": "completed"}],
                                            integrator_result={
                                                "final_output": "# Integrated",
                                                "state_snapshot": {},
                                                "report_paths": ["/mnt/user-data/outputs/cross_validation_report.md"],
                                            },
                                            integrator_spec=integrator_spec,
                                            latency_ms=20.0,
                                            run_metadata={"source": "test"},
                                        )

        self.assertIs(create_report.call_args.args[0], parent_task)
        self.assertEqual(analysis_rows[0]["task"], parent_task)
        self.assertEqual(analysis_rows[0]["analysis_type"], "CROSS")
        self.assertEqual(step_rows[0]["task"], parent_task)


class CrossValidationEnqueueTests(SimpleTestCase):
    def test_enqueue_cross_validation_run_records_queued_log_without_running_models(self):
        task = SimpleNamespace(
            id=11,
            user=SimpleNamespace(id=3),
            parent_task_id=None,
            title="Acme 深度调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={},
            progress={},
            status="COMPLETED",
            conversation=None,
        )
        specs = [
            CrossModelSpec("model-a", "model-a", "env", {"model": "model-a", "api_key": "k", "base_url": "u"}, order=1),
            CrossModelSpec("model-b", "model-b", "env", {"model": "model-b", "api_key": "k", "base_url": "u"}, order=2),
        ]
        created_logs = []
        submitted = {}

        class FakeTaskManager:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return self

            def first(self):
                return task

        class FakeStepQuery:
            def exists(self):
                return False

        class FakeStepManager:
            def filter(self, **kwargs):
                return FakeStepQuery()

            def create(self, **kwargs):
                created_logs.append(kwargs)
                return SimpleNamespace(**kwargs)

        def fake_submit(fn, *args, **kwargs):
            submitted["fn"] = fn
            submitted["args"] = args
            submitted["kwargs"] = kwargs
            return object()

        with patch.object(cross_orchestrator.ResearchTask, "objects", FakeTaskManager()):
            with patch.object(cross_orchestrator.TaskStepLog, "objects", FakeStepManager()):
                with patch.object(cross_orchestrator, "resolve_cross_model_specs", return_value=specs):
                    with patch.object(cross_orchestrator, "resolve_integrator_model_spec", return_value=specs[0]):
                        with patch.object(cross_orchestrator, "_update_cross_progress") as update_progress:
                            with patch.object(cross_orchestrator._CROSS_EXECUTOR, "submit", side_effect=fake_submit):
                                success, message, run_id = enqueue_cross_validation_run(
                                    task.id,
                                    requested_model_ids=["model-a", "model-b"],
                                )

        self.assertTrue(success, message)
        self.assertIsNotNone(run_id)
        self.assertEqual(submitted["fn"], cross_orchestrator._run_cross_validation)
        self.assertEqual(created_logs[0]["step_name"], "多模型交叉验证")
        self.assertEqual(created_logs[0]["step_status"], "RUNNING")
        self.assertEqual(created_logs[0]["detail"]["status"], "queued")
        self.assertEqual(created_logs[0]["detail"]["model_count"], 2)
        update_progress.assert_called_once()

    def test_enqueue_cross_validation_requires_completed_parent_task(self):
        task = SimpleNamespace(
            id=12,
            user=SimpleNamespace(id=3),
            parent_task_id=None,
            search_params={},
            status="ANALYZING",
            conversation=None,
        )

        class FakeTaskManager:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return self

            def first(self):
                return task

        with patch.object(cross_orchestrator.ResearchTask, "objects", FakeTaskManager()):
            success, message, run_id = enqueue_cross_validation_run(task.id)

        self.assertFalse(success)
        self.assertIn("主任务完成后", message)
        self.assertIsNone(run_id)

    def test_enqueue_cross_validation_rejects_running_parent_conversation(self):
        task = SimpleNamespace(
            id=13,
            user=SimpleNamespace(id=3),
            parent_task_id=None,
            search_params={},
            status="COMPLETED",
            conversation=SimpleNamespace(status="RUNNING"),
        )

        class FakeTaskManager:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return self

            def first(self):
                return task

        with patch.object(cross_orchestrator.ResearchTask, "objects", FakeTaskManager()):
            success, message, run_id = enqueue_cross_validation_run(task.id)

        self.assertFalse(success)
        self.assertIn("仍在运行", message)
        self.assertIsNone(run_id)
