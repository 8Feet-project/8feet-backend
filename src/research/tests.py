from django.test import SimpleTestCase

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
