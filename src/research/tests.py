import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

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
    build_initial_prompt,
    build_research_system_message,
    _resolve_max_turns,
)
from research.interface.research_interface import infer_object_type
from research.api.research_api import (
    _agent_step_status,
    _is_hidden_workflow_log,
    _progress_model,
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

    def test_standard_research_uses_default_turn_budget(self):
        self.assertEqual(_resolve_max_turns({}), 18)

    def test_deep_research_keeps_larger_turn_budget(self):
        self.assertEqual(_resolve_max_turns({"research_depth": "deep"}), 24)

    def test_execution_constraints_do_not_limit_tool_call_counts(self):
        constraints = _build_execution_constraints({})

        self.assertIn("执行建议", constraints)
        self.assertIn("web_search 用于发现候选网址", constraints)
        self.assertIn("web_fetch 用于读取候选网页", constraints)
        self.assertIn("task 子代理:", constraints)
        self.assertIn("自行判断", constraints)
        self.assertIn("deep-search 或 researcher", constraints)
        self.assertIn("bash 仅在需要处理本地文件", constraints)
        self.assertNotIn("最多调用", constraints)
        self.assertNotIn("task 子代理最多调用", constraints)
        self.assertNotIn("工具预算", constraints)
        self.assertIn("不要按来源数量机械停止", constraints)
        self.assertNotIn("3 个以上可用来源", constraints)
        self.assertIn("直接基于已有证据输出阶段性最终报告", constraints)

    def test_subagents_can_be_disabled_by_task_params(self):
        constraints = _build_execution_constraints({"enable_subagents": False})

        self.assertIn("task 子代理:", constraints)
        self.assertIn("当前参数禁用了子代理", constraints)

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

        self.assertIn("先 search", contract)
        self.assertIn("再 research", contract)
        self.assertIn("canonical URL 去重", contract)
        self.assertIn("authority_score", contract)
        self.assertIn("优先官方披露、监管机构、交易所", contract)
        self.assertIn("稳定 cite key", contract)
        self.assertIn("`deep-search`", contract)
        self.assertIn("`researcher`", contract)
        self.assertIn("不要按来源数量机械停止", contract)

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
            self.assertIn("根据任务复杂度判断", text)
            self.assertIn("DeepSearch 工作流建议", text)
            self.assertIn("`deep-search`", text)
            self.assertIn("`researcher`", text)
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
        for step_name in ("开始执行调研", "生成回答", "调研完成"):
            with self.subTest(step_name=step_name):
                self.assertTrue(
                    _is_hidden_workflow_log(
                        {
                            "step_name": step_name,
                            "detail": {"event_type": "message"},
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

    def test_agent_step_status_ignores_planning_status(self):
        status = _agent_step_status(
            [
                {"node_kind": "planning", "node_status": "running"},
                {"node_kind": "tool_call", "node_status": "completed"},
                {"node_kind": "tool_return", "node_status": "completed"},
            ]
        )

        self.assertEqual(status, "completed")

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
