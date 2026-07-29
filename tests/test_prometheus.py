from __future__ import annotations

import json
import threading
import unittest
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from rollout_guard.models import PrometheusSettings
from rollout_guard.prometheus import PrometheusClient, PrometheusError


class _Handler(BaseHTTPRequestHandler):
    response_status = 200
    response_payload: object = {}
    last_path = ""
    last_authorization: str | None = None

    def do_GET(self) -> None:
        type(self).last_path = self.path
        type(self).last_authorization = self.headers.get("Authorization")
        body = json.dumps(type(self).response_payload).encode()
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class PrometheusClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _Handler.response_status = 200
        _Handler.last_authorization = None
        _Handler.response_payload = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"service": "checkout"},
                        "values": [[100, "0.01"], [115, "0.02"]],
                    }
                ],
            },
        }

    def test_queries_range_endpoint_and_parses_samples(self) -> None:
        client = PrometheusClient(
            PrometheusSettings(url=self.base_url),
            bearer_token="secret-token",
        )
        series = client.query_range(
            'rate(http_requests_total{service="checkout"}[1m])',
            start=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
            end=datetime(2026, 7, 29, 12, 5, tzinfo=timezone.utc),
            step_seconds=15,
        )

        parsed = urllib.parse.urlparse(_Handler.last_path)
        parameters = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/v1/query_range")
        self.assertEqual(parameters["step"], ["15"])
        self.assertIn('service="checkout"', parameters["query"][0])
        self.assertEqual(_Handler.last_authorization, "Bearer secret-token")
        self.assertEqual([sample.value for sample in series[0].samples], [0.01, 0.02])

    def test_reports_prometheus_query_errors(self) -> None:
        _Handler.response_payload = {
            "status": "error",
            "errorType": "bad_data",
            "error": "parse error",
        }
        client = PrometheusClient(PrometheusSettings(url=self.base_url))

        with self.assertRaisesRegex(PrometheusError, "bad_data"):
            client.query_range(
                "bad query",
                start=datetime.now(timezone.utc),
                end=datetime.now(timezone.utc),
                step_seconds=15,
            )

    def test_rejects_nan_instead_of_silently_comparing_it(self) -> None:
        _Handler.response_payload = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {}, "values": [[100, "NaN"]]}],
            },
        }
        client = PrometheusClient(PrometheusSettings(url=self.base_url))

        with self.assertRaisesRegex(PrometheusError, "NaN or infinite"):
            client.query_range(
                "vector(0/0)",
                start=datetime.now(timezone.utc),
                end=datetime.now(timezone.utc),
                step_seconds=15,
            )

    def test_converts_socket_timeout_to_domain_error(self) -> None:
        client = PrometheusClient(
            PrometheusSettings(url=self.base_url, timeout_seconds=2)
        )

        with (
            patch("urllib.request.urlopen", side_effect=TimeoutError),
            self.assertRaisesRegex(PrometheusError, "timed out after 2s"),
        ):
            client.query_range(
                "vector(1)",
                start=datetime.now(timezone.utc),
                end=datetime.now(timezone.utc),
                step_seconds=15,
            )


if __name__ == "__main__":
    unittest.main()
