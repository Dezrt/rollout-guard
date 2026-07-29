from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Any

from rollout_guard.models import Check, Config, PrometheusSettings, ReleaseContext


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


_DURATION_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w)")
_DURATION_FACTORS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}
_COMPARATORS = {"lt", "lte", "gt", "gte", "eq", "neq"}
_REDUCERS = {"avg", "max", "min", "last", "sum"}
_NO_DATA_POLICIES = {"fail", "pass", "error"}


def parse_duration(value: str) -> float:
    if not isinstance(value, str) or not value:
        raise ConfigError("duration must be a non-empty string such as '5m' or '1m30s'")

    position = 0
    total = 0.0
    while position < len(value):
        match = _DURATION_TOKEN.match(value, position)
        if match is None:
            raise ConfigError(f"invalid duration {value!r}")
        amount, unit = match.groups()
        total += float(amount) * _DURATION_FACTORS[unit]
        position = match.end()

    if total <= 0 or not math.isfinite(total):
        raise ConfigError("duration must be greater than zero")
    return total


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    _reject_unknown(raw, {"prometheus", "release", "checks"}, "top level")

    prometheus_raw = _require_table(raw, "prometheus")
    release_raw = _require_table(raw, "release")
    checks_raw = raw.get("checks")
    if not isinstance(checks_raw, list) or not checks_raw:
        raise ConfigError("'checks' must contain at least one [[checks]] table")

    prometheus = _parse_prometheus(prometheus_raw)
    release = _parse_release(release_raw)
    checks = tuple(_parse_check(item, index) for index, item in enumerate(checks_raw, 1))

    names = [check.name for check in checks]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"check names must be unique: {', '.join(duplicates)}")

    return Config(prometheus=prometheus, release=release, checks=checks)


def _parse_prometheus(raw: dict[str, Any]) -> PrometheusSettings:
    _reject_unknown(
        raw,
        {"url", "timeout_seconds", "verify_tls", "bearer_token_env"},
        "[prometheus]",
    )
    url = _require_string(raw, "url", "[prometheus]").rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("[prometheus].url must start with http:// or https://")

    timeout = raw.get("timeout_seconds", 5.0)
    if not _is_number(timeout) or float(timeout) <= 0:
        raise ConfigError("[prometheus].timeout_seconds must be greater than zero")

    verify_tls = raw.get("verify_tls", True)
    if not isinstance(verify_tls, bool):
        raise ConfigError("[prometheus].verify_tls must be a boolean")

    token_env = raw.get("bearer_token_env")
    if token_env is not None and (not isinstance(token_env, str) or not token_env):
        raise ConfigError("[prometheus].bearer_token_env must be a non-empty string")

    return PrometheusSettings(
        url=url,
        timeout_seconds=float(timeout),
        verify_tls=verify_tls,
        bearer_token_env=token_env,
    )


def _parse_release(raw: dict[str, Any]) -> ReleaseContext:
    _reject_unknown(
        raw,
        {"service", "environment", "candidate", "window", "step"},
        "[release]",
    )
    window = parse_duration(_require_string(raw, "window", "[release]"))
    step = parse_duration(_require_string(raw, "step", "[release]"))
    if step > window:
        raise ConfigError("[release].step cannot be greater than [release].window")

    return ReleaseContext(
        service=_require_string(raw, "service", "[release]"),
        environment=_require_string(raw, "environment", "[release]"),
        candidate=_require_string(raw, "candidate", "[release]"),
        window_seconds=window,
        step_seconds=step,
    )


def _parse_check(raw: Any, index: int) -> Check:
    context = f"[[checks]] #{index}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be a table")
    _reject_unknown(
        raw,
        {
            "name",
            "query",
            "comparator",
            "threshold",
            "reducer",
            "on_no_data",
            "allow_multiple_series",
        },
        context,
    )

    comparator = raw.get("comparator")
    if comparator not in _COMPARATORS:
        raise ConfigError(
            f"{context}.comparator must be one of {', '.join(sorted(_COMPARATORS))}"
        )

    reducer = raw.get("reducer", "max")
    if reducer not in _REDUCERS:
        raise ConfigError(f"{context}.reducer must be one of {', '.join(sorted(_REDUCERS))}")

    on_no_data = raw.get("on_no_data", "error")
    if on_no_data not in _NO_DATA_POLICIES:
        raise ConfigError(
            f"{context}.on_no_data must be one of {', '.join(sorted(_NO_DATA_POLICIES))}"
        )

    threshold = raw.get("threshold")
    if not _is_number(threshold) or not math.isfinite(float(threshold)):
        raise ConfigError(f"{context}.threshold must be a finite number")

    allow_multiple = raw.get("allow_multiple_series", False)
    if not isinstance(allow_multiple, bool):
        raise ConfigError(f"{context}.allow_multiple_series must be a boolean")

    return Check(
        name=_require_string(raw, "name", context),
        query=_require_string(raw, "query", context),
        comparator=comparator,
        threshold=float(threshold),
        reducer=reducer,
        on_no_data=on_no_data,
        allow_multiple_series=allow_multiple,
    )


def _require_table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"'[{key}]' table is required")
    return value


def _require_string(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _reject_unknown(raw: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {context}: {', '.join(unknown)}")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

