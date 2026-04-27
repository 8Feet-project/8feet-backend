from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from research.interface import cross_validation_runtime
from research.interface.cross_validation_runtime import (
    CrossModelSpec,
    _coerce_model_id_list,
    _payload_from_result,
    build_cross_integrator_prompt,
    build_cross_model_research_prompt,
    enqueue_cross_validation_run,
)
from research.interface.research_runtime import (
    _build_execution_constraints,
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


class CrossValidationRuntimeTests(SimpleTestCase):
    def test_model_id_list_accepts_json_array_and_comma_text(self):
        self.assertEqual(_coerce_model_id_list('["a", "b"]'), ["a", "b"])
        self.assertEqual(_coerce_model_id_list("a,b, c "), ["a", "b", "c"])
        self.assertEqual(_coerce_model_id_list({"models": ["m1", "m2"]}), ["m1", "m2"])

    def test_cross_model_prompt_requires_independent_presented_report(self):
        task = SimpleNamespace(title="T", object_name="Acme", object_type="COMPANY")
        prompt = build_cross_model_research_prompt(task, "base task")

        self.assertIn("独立调研线程", prompt)
        self.assertIn("/mnt/user-data/outputs/model_research_report.md", prompt)
        self.assertIn("present_report", prompt)

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
        self.assertIn("cross_validation_report.md", prompt)
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


class CrossValidationEnqueueTests(SimpleTestCase):
    def test_enqueue_cross_validation_run_records_queued_log_without_running_models(self):
        task = SimpleNamespace(
            id=11,
            user=SimpleNamespace(id=3),
            title="Acme 深度调研",
            object_name="Acme",
            object_type="COMPANY",
            search_params={},
            progress={},
            status="COMPLETED",
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

        with patch.object(cross_validation_runtime.ResearchTask, "objects", FakeTaskManager()):
            with patch.object(cross_validation_runtime.TaskStepLog, "objects", FakeStepManager()):
                with patch.object(cross_validation_runtime, "resolve_cross_model_specs", return_value=specs):
                    with patch.object(cross_validation_runtime, "resolve_integrator_model_spec", return_value=specs[0]):
                        with patch.object(cross_validation_runtime, "_update_cross_progress") as update_progress:
                            with patch.object(cross_validation_runtime._CROSS_EXECUTOR, "submit", side_effect=fake_submit):
                                success, message, run_id = enqueue_cross_validation_run(
                                    task.id,
                                    requested_model_ids=["model-a", "model-b"],
                                )

        self.assertTrue(success, message)
        self.assertIsNotNone(run_id)
        self.assertEqual(submitted["fn"], cross_validation_runtime._run_cross_validation)
        self.assertEqual(created_logs[0]["step_name"], "多模型交叉验证")
        self.assertEqual(created_logs[0]["step_status"], "RUNNING")
        self.assertEqual(created_logs[0]["detail"]["status"], "queued")
        self.assertEqual(created_logs[0]["detail"]["model_count"], 2)
        update_progress.assert_called_once()
