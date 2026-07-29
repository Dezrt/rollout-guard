from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rollout_guard.cli import EXIT_ERROR, EXIT_PROMOTE, main
from rollout_guard.prometheus import Sample, Series


CONFIG = """
[prometheus]
url = "http://prometheus:9090"
bearer_token_env = "PROM_TOKEN"

[release]
service = "checkout"
environment = "production"
candidate = "v2"
window = "1m"
step = "15s"

[[checks]]
name = "error rate"
query = "vector(0.001)"
comparator = "lte"
threshold = 0.01
"""


class CLITests(unittest.TestCase):
    def test_json_output_and_promote_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guard.toml"
            path.write_text(CONFIG, encoding="utf-8")
            stdout = io.StringIO()
            series = (Series(labels={}, samples=(Sample(1, 0.001),)),)

            with (
                patch.dict(os.environ, {"PROM_TOKEN": "secret"}),
                patch(
                    "rollout_guard.prometheus.PrometheusClient.query_range",
                    return_value=series,
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "--config",
                        str(path),
                        "--output",
                        "json",
                        "--now",
                        "2026-07-29T12:00:00Z",
                    ]
                )

        self.assertEqual(exit_code, EXIT_PROMOTE)
        self.assertIn('"decision": "promote"', stdout.getvalue())

    def test_missing_token_is_a_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guard.toml"
            path.write_text(CONFIG, encoding="utf-8")
            stderr = io.StringIO()

            with (
                patch.dict(os.environ, {}, clear=True),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["--config", str(path)])

        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("PROM_TOKEN", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

