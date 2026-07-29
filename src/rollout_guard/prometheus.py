from __future__ import annotations

import json
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rollout_guard.models import PrometheusSettings


class PrometheusError(RuntimeError):
    """Raised when Prometheus cannot return a usable query result."""


@dataclass(frozen=True)
class Sample:
    timestamp: float
    value: float


@dataclass(frozen=True)
class Series:
    labels: dict[str, str]
    samples: tuple[Sample, ...]


class PrometheusClient:
    def __init__(
        self,
        settings: PrometheusSettings,
        *,
        bearer_token: str | None = None,
    ) -> None:
        self._settings = settings
        self._bearer_token = bearer_token

    def query_range(
        self,
        query: str,
        *,
        start: datetime,
        end: datetime,
        step_seconds: float,
    ) -> tuple[Series, ...]:
        parameters = urllib.parse.urlencode(
            {
                "query": query,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": f"{step_seconds:g}",
            }
        )
        url = f"{self._settings.url}/api/v1/query_range?{parameters}"
        headers = {"Accept": "application/json", "User-Agent": "rollout-guard/0.1"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"

        request = urllib.request.Request(url, headers=headers)
        context = None
        if url.startswith("https://") and not self._settings.verify_tls:
            context = ssl._create_unverified_context()

        try:
            with urllib.request.urlopen(
                request,
                timeout=self._settings.timeout_seconds,
                context=context,
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read(512).decode("utf-8", errors="replace")
            raise PrometheusError(f"Prometheus returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise PrometheusError(f"cannot reach Prometheus: {exc.reason}") from exc
        except TimeoutError as exc:
            raise PrometheusError(
                f"Prometheus request timed out after {self._settings.timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise PrometheusError(f"Prometheus connection failed: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PrometheusError("Prometheus returned invalid JSON") from exc

        return self._parse_matrix(payload)

    @staticmethod
    def _parse_matrix(payload: Any) -> tuple[Series, ...]:
        if not isinstance(payload, dict):
            raise PrometheusError("Prometheus response must be a JSON object")
        if payload.get("status") != "success":
            error_type = payload.get("errorType", "unknown")
            error = payload.get("error", "query failed")
            raise PrometheusError(f"Prometheus query failed ({error_type}): {error}")

        data = payload.get("data")
        if not isinstance(data, dict) or data.get("resultType") != "matrix":
            result_type = data.get("resultType") if isinstance(data, dict) else None
            raise PrometheusError(f"expected matrix result, got {result_type!r}")

        raw_result = data.get("result")
        if not isinstance(raw_result, list):
            raise PrometheusError("Prometheus matrix result must be an array")

        series: list[Series] = []
        for index, item in enumerate(raw_result):
            if not isinstance(item, dict):
                raise PrometheusError(f"series #{index + 1} must be an object")
            raw_labels = item.get("metric", {})
            raw_values = item.get("values")
            if not isinstance(raw_labels, dict) or not isinstance(raw_values, list):
                raise PrometheusError(f"series #{index + 1} has an invalid shape")

            labels = {str(key): str(value) for key, value in raw_labels.items()}
            samples: list[Sample] = []
            for raw_sample in raw_values:
                if not isinstance(raw_sample, list) or len(raw_sample) != 2:
                    raise PrometheusError(f"series #{index + 1} contains an invalid sample")
                try:
                    timestamp = float(raw_sample[0])
                    value = float(raw_sample[1])
                except (TypeError, ValueError) as exc:
                    raise PrometheusError(
                        f"series #{index + 1} contains a non-numeric sample"
                    ) from exc
                if not math.isfinite(timestamp) or not math.isfinite(value):
                    raise PrometheusError(
                        f"series #{index + 1} contains NaN or infinite data"
                    )
                samples.append(Sample(timestamp=timestamp, value=value))
            series.append(Series(labels=labels, samples=tuple(samples)))

        return tuple(series)
