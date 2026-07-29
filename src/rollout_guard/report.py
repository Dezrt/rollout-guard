from __future__ import annotations

import json

from rollout_guard.models import DecisionReport

_HEADINGS = {
    "promote": "ПРОДОЛЖИТЬ",
    "rollback": "ОТКАТИТЬ",
    "error": "НЕТ РЕШЕНИЯ",
}


def render_text(report: DecisionReport) -> str:
    lines = [
        f"Решение по релизу: {_HEADINGS[report.decision]}",
        (
            f"Релиз: {report.service} {report.candidate} -> "
            f"{report.environment}"
        ),
        f"Окно: {report.started_at.isoformat()} .. {report.ended_at.isoformat()}",
        "",
    ]

    for result in report.results:
        lines.append(f"[{result.status.upper():5}] {result.name}")
        lines.append(
            f"        {result.message}; агрегация={result.reducer}, "
            f"точек={result.sample_count}, рядов={result.series_count}"
        )

    return "\n".join(lines)


def render_json(report: DecisionReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)
