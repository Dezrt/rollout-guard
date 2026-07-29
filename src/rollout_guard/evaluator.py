from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from string import Template
from typing import Callable

from rollout_guard.models import (
    Check,
    CheckResult,
    CheckStatus,
    Comparator,
    Config,
    DecisionReport,
)
from rollout_guard.prometheus import PrometheusClient, PrometheusError

_COMPARATORS: dict[Comparator, Callable[[float, float], bool]] = {
    "lt": lambda observed, threshold: observed < threshold,
    "lte": lambda observed, threshold: observed <= threshold,
    "gt": lambda observed, threshold: observed > threshold,
    "gte": lambda observed, threshold: observed >= threshold,
    "eq": lambda observed, threshold: math.isclose(observed, threshold),
    "neq": lambda observed, threshold: not math.isclose(observed, threshold),
}
_COMPARATOR_SYMBOLS: dict[Comparator, str] = {
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "eq": "==",
    "neq": "!=",
}


def evaluate(
    config: Config,
    client: PrometheusClient,
    *,
    now: datetime | None = None,
) -> DecisionReport:
    ended_at = now or datetime.now(timezone.utc)
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    started_at = ended_at - timedelta(seconds=config.release.window_seconds)

    results = tuple(
        _evaluate_check(
            check,
            config=config,
            client=client,
            started_at=started_at,
            ended_at=ended_at,
        )
        for check in config.checks
    )

    if any(result.status == "error" for result in results):
        decision = "error"
    elif any(result.status == "fail" for result in results):
        decision = "rollback"
    else:
        decision = "promote"

    return DecisionReport(
        decision=decision,
        service=config.release.service,
        environment=config.release.environment,
        candidate=config.release.candidate,
        started_at=started_at,
        ended_at=ended_at,
        results=results,
    )


def _evaluate_check(
    check: Check,
    *,
    config: Config,
    client: PrometheusClient,
    started_at: datetime,
    ended_at: datetime,
) -> CheckResult:
    try:
        query = Template(check.query).substitute(config.release.template_values())
    except (KeyError, ValueError) as exc:
        return _error_result(check, check.query, f"ошибка в шаблоне запроса: {exc}")

    try:
        series = client.query_range(
            query,
            start=started_at,
            end=ended_at,
            step_seconds=config.release.step_seconds,
        )
    except PrometheusError as exc:
        return _error_result(check, query, str(exc))

    if len(series) > 1 and not check.allow_multiple_series:
        return _error_result(
            check,
            query,
            (
                f"запрос вернул рядов: {len(series)}; агрегируйте их в PromQL или задайте "
                "allow_multiple_series = true"
            ),
            series_count=len(series),
        )

    samples = [sample for item in series for sample in item.samples]
    if not samples:
        status = check.on_no_data
        if status == "pass":
            return _result(
                check,
                status="pass",
                query=query,
                observed=None,
                sample_count=0,
                series_count=len(series),
                message="данных нет; это разрешено политикой",
            )
        if status == "fail":
            return _result(
                check,
                status="fail",
                query=query,
                observed=None,
                sample_count=0,
                series_count=len(series),
                message="данных нет; релиз отклонён политикой",
            )
        return _error_result(
            check,
            query,
            "запрос не вернул ни одной точки",
            series_count=len(series),
        )

    values = [sample.value for sample in samples]
    observed = (
        max(samples, key=lambda sample: sample.timestamp).value
        if check.reducer == "last"
        else _reduce(values, check.reducer)
    )
    passed = _COMPARATORS[check.comparator](observed, check.threshold)
    symbol = _COMPARATOR_SYMBOLS[check.comparator]
    message = (
        f"значение {observed:g} {symbol} порог {check.threshold:g}"
        if passed
        else f"значение {observed:g} нарушает условие {symbol} {check.threshold:g}"
    )
    return _result(
        check,
        status="pass" if passed else "fail",
        query=query,
        observed=observed,
        sample_count=len(samples),
        series_count=len(series),
        message=message,
    )


def _reduce(values: list[float], reducer: str) -> float:
    if reducer == "avg":
        return sum(values) / len(values)
    if reducer == "max":
        return max(values)
    if reducer == "min":
        return min(values)
    if reducer == "sum":
        return sum(values)
    raise AssertionError(f"unsupported reducer: {reducer}")


def _error_result(
    check: Check,
    query: str,
    message: str,
    *,
    series_count: int = 0,
) -> CheckResult:
    return _result(
        check,
        status="error",
        query=query,
        observed=None,
        sample_count=0,
        series_count=series_count,
        message=message,
    )


def _result(
    check: Check,
    *,
    status: CheckStatus,
    query: str,
    observed: float | None,
    sample_count: int,
    series_count: int,
    message: str,
) -> CheckResult:
    return CheckResult(
        name=check.name,
        status=status,
        query=query,
        comparator=check.comparator,
        threshold=check.threshold,
        reducer=check.reducer,
        observed=observed,
        sample_count=sample_count,
        series_count=series_count,
        message=message,
    )
