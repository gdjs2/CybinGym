import unittest
from unittest.mock import patch

import cybingym
from solvers import swe_agents


class KimiCodeSolverTests(unittest.TestCase):
    def test_kimi_code_solver_uses_shared_bridge_and_pinned_version(self):
        sentinel = object()
        with patch.object(swe_agents, "kimi_code", return_value=sentinel) as agent:
            solvers = swe_agents.kimi_code_solver()

        self.assertIs(solvers[1], sentinel)
        kwargs = agent.call_args.kwargs
        self.assertEqual(kwargs["disallowed_tools"], ["WebSearch", "FetchURL"])
        self.assertEqual(kwargs["version"], "0.29.0")
        self.assertEqual(len(kwargs["bridged_tools"]), 1)
        self.assertEqual(kwargs["bridged_tools"][0].name, "bash_tools_for_target_and_defaults")

    def test_cybingym_selects_kimi_code_solver_with_version(self):
        sentinel = object()
        with patch.object(cybingym, "kimi_code_solver", return_value=sentinel) as solver:
            selected = cybingym._select_solver("kimi_code", kimi_code_version="0.30.0")

        self.assertIs(selected, sentinel)
        solver.assert_called_once_with(version="0.30.0")
