from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from rollout_guard import __version__
from rollout_guard.config import ConfigError, load_config
from rollout_guard.evaluator import evaluate
from rollout_guard.models import Config, PrometheusSettings
from rollout_guard.prometheus import PrometheusClient
from rollout_guard.report import render_json, render_text

EXIT_PROMOTE = 0
EXIT_ERROR = 1
EXIT_ROLLBACK = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollout-guard",
        description="Проверить SLO релиза по данным Prometheus.",
        add_help=False,
    )
    parser._optionals.title = "параметры"
    parser.add_argument("-h", "--help", action="help", help="Показать эту справку")
    parser.add_argument(
        "--config",
        required=True,
        metavar="ФАЙЛ",
        help="Путь к TOML-конфигурации",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Формат отчёта (по умолчанию: text)",
    )
    parser.add_argument(
        "--prometheus-url",
        metavar="URL",
        help="Переопределить [prometheus].url без изменения конфигурации",
    )
    parser.add_argument(
        "--now",
        metavar="ВРЕМЯ",
        help="Зафиксировать время в RFC3339 для повтора проверки или тестов",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="Показать версию",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.prometheus_url:
            config = _with_prometheus_url(config, args.prometheus_url)
        now = _parse_now(args.now)
        token = _load_bearer_token(config)
    except ConfigError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        return EXIT_ERROR

    client = PrometheusClient(config.prometheus, bearer_token=token)
    report = evaluate(config, client, now=now)
    print(render_json(report) if args.output == "json" else render_text(report))

    if report.decision == "promote":
        return EXIT_PROMOTE
    if report.decision == "rollback":
        return EXIT_ROLLBACK
    return EXIT_ERROR


def entrypoint() -> None:
    raise SystemExit(main())


def _with_prometheus_url(config: Config, url: str) -> Config:
    if not url.startswith(("http://", "https://")):
        raise ConfigError("--prometheus-url must start with http:// or https://")
    settings = PrometheusSettings(
        url=url.rstrip("/"),
        timeout_seconds=config.prometheus.timeout_seconds,
        verify_tls=config.prometheus.verify_tls,
        bearer_token_env=config.prometheus.bearer_token_env,
    )
    return Config(prometheus=settings, release=config.release, checks=config.checks)


def _load_bearer_token(config: Config) -> str | None:
    name = config.prometheus.bearer_token_env
    if name is None:
        return None
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"environment variable {name!r} is required but empty")
    return value


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ConfigError("--now must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigError("--now must include a timezone")
    return parsed
