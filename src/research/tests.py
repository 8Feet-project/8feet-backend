import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from research.interface.cross_validation import model_specs as cross_model_specs
from research.interface.cross_validation import orchestrator as cross_orchestrator
from research.interface.cross_validation import result_records as cross_result_records
from research.interface.cross_validation import task_records as cross_task_records
from research.interface.cross_validation.artifacts import (
    _is_llm_failure_output,
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
    object_type_research_requirements,
    report_format_requirements,
)
from research.interface.research_runtime import (
    _build_execution_constraints,
    build_initial_prompt,
    build_research_system_message,
    _resolve_max_turns,
    _resolve_tool_limits,
)


class ResearchRuntimeConstraintTests(SimpleTestCase):
    def test_standard_research_defaults_are_budgeted(self):
        self.assertEqual(_resolve_max_turns({}), 10)
        self.assertEqual(
            _resolve_tool_limits({}),
            {
                "web_search": 5,
                "web_fetch": 6,
                "task": 0,
                "bash": 0,
            },
        )

    def test_deep_research_keeps_larger_budget(self):
        self.assertEqual(_resolve_max_turns({"research_depth": "deep"}), 18)
        self.assertEqual(
            _resolve_tool_limits({"research_depth": "deep"}),
            {
                "web_search": 12,
                "web_fetch": 16,
                "task": 4,
                "bash": 2,
            },
        )

    def test_execution_constraints_tell_agent_to_finish_report(self):
        constraints = _build_execution_constraints({})

        self.assertIn("web_search 最多调用 5 次", constraints)
        self.assertIn("web_fetch 最多调用 6 次", constraints)
        self.assertIn("禁止启动子代理", constraints)
        self.assertIn("直接基于已有证据输出阶段性最终报告", constraints)

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
        contract = report_format_requirements("/mnt/user-data/outputs/research_report.md")

        self.assertIn("PDF/Word 导出", contract)
        self.assertIn("## 摘要", contract)
        self.assertIn("## 核心发现", contract)
        self.assertIn("## 关键证据", contract)
        self.assertIn("## 风险与不确定性", contract)
        self.assertIn("## 结论与建议", contract)
        self.assertIn("/mnt/user-data/outputs/research_report.md", contract)
        self.assertIn("present_report", contract)

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
            self.assertIn("最终报告格式与交付要求", text)
            self.assertIn("PDF/Word 导出", text)
            self.assertIn("## 摘要", text)
            self.assertIn("## 结论与建议", text)
            self.assertIn("present_report", text)


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
        self.assertIn("最终报告格式与交付要求", prompt)
        self.assertIn("## 摘要", prompt)
        self.assertIn("## 结论与建议", prompt)
        self.assertIn("present_report", prompt)

    def test_integrator_system_message_requires_report_contract(self):
        system_message = build_cross_integrator_system_message()

        self.assertIn("最终报告格式与交付要求", system_message)
        self.assertIn("PDF/Word 导出", system_message)
        self.assertIn("## 核心发现", system_message)
        self.assertIn("/mnt/user-data/outputs/cross_validation_report.md", system_message)
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
        self.assertIn("最终报告格式与交付要求", prompt)
        self.assertIn("## 风险与不确定性", prompt)
        self.assertIn("智能整合优化", prompt)

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
                    "report_paths": ["/mnt/user-data/outputs/cross_validation_report.md"],
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

    def test_provider_failure_text_is_not_treated_as_report(self):
        self.assertTrue(
            _is_llm_failure_output(
                "The configured LLM provider is temporarily unavailable after multiple retries."
            )
        )
        self.assertFalse(_is_llm_failure_output("# 正常报告\n- 结论"))

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
