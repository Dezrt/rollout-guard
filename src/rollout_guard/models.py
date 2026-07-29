from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

Comparator = Literal["lt", "lte", "gt", "gte", "eq", "neq"]
Reducer = Literal["avg", "max", "min", "last", "sum"]
NoDataPolicy = Literal["fail", "pass", "error"]
CheckStatus = Literal["pass", "fail", "error"]
Decision = Literal["promote", "rollback", "error"]


@dataclass(frozen=True)
class PrometheusSettings:
    url: str
    timeout_seconds: float = 5.0
    verify_tls: bool = True
    bearer_token_env: str | None = None


@dataclass(frozen=True)
class ReleaseContext:
    service: str
    environment: str
    candidate: str
    window_seconds: float
    step_seconds: float

    def template_values(self) -> dict[str, str]:
        return {
            "service": self.service,
            "environment": self.environment,
            "candidate": self.candidate,
        }


@dataclass(frozen=True)
class Check:
    name: str
    query: str
    comparator: Comparator
    threshold: float
    reducer: Reducer = "max"
    on_no_data: NoDataPolicy = "error"
    allow_multiple_series: bool = False


@dataclass(frozen=True)
class Config:
    prometheus: PrometheusSettings
    release: ReleaseContext
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    query: str
    comparator: Comparator
    threshold: float
    reducer: Reducer
    observed: float | None
    sample_count: int
    series_count: int
    message: str


@dataclass(frozen=True)
class DecisionReport:
    decision: Decision
    service: str
    environment: str
    candidate: str
    started_at: datetime
    ended_at: datetime
    results: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["ended_at"] = self.ended_at.isoformat()
        return payload

