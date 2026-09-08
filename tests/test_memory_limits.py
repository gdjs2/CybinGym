import shutil
import unittest
from unittest.mock import MagicMock, patch

from cybingym import create_binary_sample
from memory_limits import VALIDATION_MEMORY_KEY, sample_memory_limits
from scorer import run_docker_validation


class SampleMemoryTests(unittest.TestCase):
    def test_budget_is_enforced_in_full_and_crash_compose(self):
        for level in ('full', 'crash'):
            for budget in (4096, 6144):
                with self.subTest(level=level, budget=budget):
                    sample = create_binary_sample('test', 'test-image', evaluation_level=level,
                                                  sample_memory_mb=budget)
                    services = sample.sandbox.config.services
                    self.addCleanup(shutil.rmtree, services['default'].build.context)
                    total = 2 * sample.metadata[VALIDATION_MEMORY_KEY]
                    for service in services.values():
                        self.assertGreater(service.mem_limit, 0)
                        self.assertEqual(service.mem_limit, service.memswap_limit)
                        total += service.mem_limit
                    self.assertLessEqual(total, budget * 1024 * 1024)
                    if level == 'full':
                        self.assertEqual(total, budget * 1024 * 1024)

    def test_invalid_budgets(self):
        for value in (0, -1, 512, True, 4096.5, '4096'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sample_memory_limits(value)

    def test_validation_enforces_limit_and_reports_oom(self):
        container = MagicMock()
        container.wait.return_value = {'StatusCode': 137}
        container.attrs = {'State': {'OOMKilled': True}}
        container.logs.return_value = b''
        with patch('scorer.docker.from_env') as docker:
            docker.return_value.containers.run.return_value = container
            result = run_docker_validation('image', 'binary', '/tmp/poc', memory_limit=123456789)
            kwargs = docker.return_value.containers.run.call_args.kwargs
            self.assertEqual(kwargs['mem_limit'], 123456789)
            self.assertEqual(kwargs['memswap_limit'], 123456789)
        self.assertTrue(result['oom_killed'])
        container.remove.assert_called_once_with(force=True)
