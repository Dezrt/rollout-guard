from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rollout_guard.config import ConfigError, load_config, parse_duration


VALID_CONFIG = """
[prometheus]
url = "http://prometheus:9090/"
timeout_seconds = 3

[release]
service = "checkout"
environment = "production"
candidate = "2026.07.29-1"
window = "5m"
step = "15s"

[[checks]]
name = "error rate"
query = "vector(0.01)"
comparator = "lte"
threshold = 0.02
"""


class DurationTests(unittest.TestCase):
    def test_parses_compound_duration(self) -> None:
        self.assertEqual(parse_duration("1h30m15s"), 5415.0)

    def test_rejects_partial_duration(self) -> None:
        with self.assertRaisesRegex(ConfigError, "invalid duration"):
            parse_duration("5minutes")


class ConfigTests(unittest.TestCase):
    def test_loads_valid_config_and_applies_defaults(self) -> None:
        config = self._load(VALID_CONFIG)

        self.assertEqual(config.prometheus.url, "http://prometheus:9090")
        self.assertEqual(config.release.window_seconds, 300)
        self.assertEqual(config.checks[0].reducer, "max")
        self.assertEqual(config.checks[0].on_no_data, "error")

    def test_rejects_unknown_keys_to_catch_typos(self) -> None:
        broken = VALID_CONFIG.replace(
            'timeout_seconds = 3',
            'timeout_seconds = 3\nverify_tsl = true',
        )

        with self.assertRaisesRegex(ConfigError, "verify_tsl"):
            self._load(broken)

    def test_rejects_duplicate_check_names(self) -> None:
        duplicate = VALID_CONFIG + """
[[checks]]
name = "error rate"
query = "vector(1)"
comparator = "gte"
threshold = 1
"""

        with self.assertRaisesRegex(ConfigError, "must be unique"):
            self._load(duplicate)

    def test_rejects_step_larger_than_window(self) -> None:
        broken = VALID_CONFIG.replace('step = "15s"', 'step = "10m"')

        with self.assertRaisesRegex(ConfigError, "cannot be greater"):
            self._load(broken)

    @staticmethod
    def _load(content: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guard.toml"
            path.write_text(content, encoding="utf-8")
            return load_config(path)


if __name__ == "__main__":
    unittest.main()

