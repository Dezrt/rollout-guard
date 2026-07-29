from __future__ import annotations

import unittest
from datetime import datetime, timezone

from rollout_guard.evaluator import evaluate
from rollout_guard.models import (
    Check,
    Config,
    PrometheusSettings,
    ReleaseContext,
)
from rollout_guard.prometheus import PrometheusError, Sample, Series


class FakePrometheusClient:
    def __init__(self, responses: dict[str, tuple[Series, ...] | Exception]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def query_range(self, query, *, start, end, step_seconds):
        self.queries.append(query)
        response = self.responses[query]
        if isinstance(response, Exception):
            raise response
        return response


def make_series(*values: float) -> tuple[Series, ...]:
    return (
        Series(
            labels={},
            samples=tuple(
                Sample(timestamp=float(index), value=value)
                for index, value in enumerate(values)
            ),
        ),
    )


def make_config(*checks: Check) -> Config:
    return Config(
        prometheus=PrometheusSettings(url="http://prometheus:9090"),
        release=ReleaseContext(
            service="checkout",
            environment="production",
            candidate="v2",
            window_seconds=300,
            step_seconds=15,
        ),
        checks=checks,
    )


class EvaluatorTests(unittest.TestCase):
    def test_promotes_when_every_check_passes(self) -> None:
        checks = (
            Check(
                name="error rate",
                query='error_rate{service="${service}",version="${candidate}"}',
                comparator="lte",
                threshold=0.01,
                reducer="max",
            ),
            Check(
                name="request volume",
                query='request_count{environment="${environment}"}',
                comparator="gte",
                threshold=100,
                reducer="sum",
            ),
        )
        client = FakePrometheusClient(
            {
                'error_rate{service="checkout",version="v2"}': make_series(0.002, 0.009),
                'request_count{environment="production"}': make_series(40, 70),
            }
        )

        report = evaluate(
            make_config(*checks),
            client,
            now=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(report.decision, "promote")
        self.assertEqual([result.status for result in report.results], ["pass", "pass"])

    def test_rolls_back_when_a_threshold_is_violated(self) -> None:
        check = Check(
            name="p95 latency",
            query="latency",
            comparator="lte",
            threshold=0.5,
            reducer="max",
        )
        client = FakePrometheusClient({"latency": make_series(0.2, 0.8, 0.4)})

        report = evaluate(make_config(check), client)

        self.assertEqual(report.decision, "rollback")
        self.assertEqual(report.results[0].observed, 0.8)

    def test_no_data_policy_can_reject_without_hiding_operational_errors(self) -> None:
        reject_check = Check(
            name="required traffic",
            query="empty",
            comparator="gte",
            threshold=1,
            on_no_data="fail",
        )
        error_check = Check(
            name="broken query",
            query="broken",
            comparator="lte",
            threshold=1,
        )
        client = FakePrometheusClient(
            {
                "empty": (),
                "broken": PrometheusError("bad_data: parse error"),
            }
        )

        report = evaluate(make_config(reject_check, error_check), client)

        self.assertEqual(report.decision, "error")
        self.assertEqual(report.results[0].status, "fail")
        self.assertEqual(report.results[1].status, "error")

    def test_rejects_accidental_multiple_series(self) -> None:
        check = Check(
            name="error rate",
            query="rate",
            comparator="lte",
            threshold=0.01,
        )
        client = FakePrometheusClient(
            {
                "rate": (
                    Series(labels={"pod": "a"}, samples=(Sample(1, 0.001),)),
                    Series(labels={"pod": "b"}, samples=(Sample(1, 0.002),)),
                )
            }
        )

        report = evaluate(make_config(check), client)

        self.assertEqual(report.decision, "error")
        self.assertIn("агрегируйте их в PromQL", report.results[0].message)


if __name__ == "__main__":
    unittest.main()
