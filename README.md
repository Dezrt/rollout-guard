# Rollout Guard

[![CI](https://github.com/Dezrt/rollout-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Dezrt/rollout-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Небольшой SLO-гейт для canary-релизов. После выкладки новой версии он
запрашивает метрики из Prometheus и говорит пайплайну, что делать дальше:

- код `0` — метрики в норме, релиз можно продолжать;
- код `2` — порог нарушен, кандидата нужно откатить;
- код `1` — принять решение нельзя: сломан запрос, нет связи с Prometheus
  или пришли некорректные данные.

## Зачем я это сделал

Я собрал проект вокруг вполне практичной ситуации: Kubernetes считает rollout
успешным, Pod'ы проходят readiness-пробы, но после переключения трафика у новой
версии уже растут ошибки или задержка.

Можно было написать ещё один контроллер, но для первой версии это показалось
лишним. В моём варианте Rollout Guard — обычный короткоживущий CLI-процесс.
Его можно вызвать из CI/CD, Helm hook или Kubernetes Job. Он только принимает
решение по метрикам; переключение трафика и откат остаются в системе доставки.

## Как выглядит проверка

```text
Решение по релизу: ПРОДОЛЖИТЬ
Релиз: checkout 2026.07.29-1 -> production
Окно: 2026-07-29T11:50:00+00:00 .. 2026-07-29T12:00:00+00:00

[PASS ] доля ошибок 5xx у кандидата
        значение 0.004 <= порог 0.01; агрегация=max, точек=21, рядов=1
[PASS ] p95 задержки кандидата
        значение 0.31 <= порог 0.4; агрегация=max, точек=21, рядов=1
[PASS ] минимальный трафик кандидата
        значение 184 >= порог 100; агрегация=last, точек=21, рядов=1
```

Отсутствие данных, несколько неожиданных временных рядов, таймауты, ошибки
PromQL, `NaN` и бесконечные значения обрабатываются отдельно. Такие ситуации
нельзя незаметно принять за здоровый релиз.

## Быстрый запуск

Нужен Python 3.11 или новее.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

rollout-guard \
  --config examples/checkout-production.toml \
  --output json
```

У приложения нет сторонних runtime-зависимостей. Production-пример читает
bearer-токен из переменной `PROMETHEUS_BEARER_TOKEN`.

Локальный сценарий с тестовым Prometheus:

```bash
make demo
```

Команда поднимает Prometheus через Docker Compose, собирает контейнер от
непривилегированного пользователя и выполняет три детерминированные проверки
из [examples/demo.toml](examples/demo.toml).

## Конфигурация

```toml
[prometheus]
url = "https://prometheus.monitoring.svc.cluster.local:9090"
timeout_seconds = 5
verify_tls = true
bearer_token_env = "PROMETHEUS_BEARER_TOKEN"

[release]
service = "checkout"
environment = "production"
candidate = "2026.07.29-1"
window = "10m"
step = "30s"

[[checks]]
name = "доля ошибок 5xx у кандидата"
query = '''
sum(rate(http_requests_total{service="${service}",version="${candidate}",status=~"5.."}[2m]))
/
clamp_min(sum(rate(http_requests_total{service="${service}",version="${candidate}"}[2m])), 0.001)
'''
comparator = "lte"
threshold = 0.01
reducer = "max"
on_no_data = "error"
```

В PromQL можно использовать `${service}`, `${environment}` и `${candidate}`.

| Поле | Допустимые значения |
|---|---|
| `comparator` | `lt`, `lte`, `gt`, `gte`, `eq`, `neq` |
| `reducer` | `max`, `avg`, `min`, `last`, `sum` |
| `on_no_data` | `error`, `fail`, `pass` |

Неизвестные ключи считаются ошибкой. Это сделано специально: опечатка в
настройке не должна молча ослабить правило релиза.

## Kubernetes и Helm

Chart запускает проверку как одноразовый Job:

```bash
helm upgrade --install checkout-gate charts/rollout-guard \
  --set release.service=checkout \
  --set release.candidate=2026.07.29-1 \
  --wait
```

Для Helm-релиза Job можно включить как `post-upgrade` hook. В сочетании с
`helm upgrade --atomic` проваленная проверка завершит upgrade с ошибкой, после
чего Helm восстановит предыдущую ревизию:

```bash
helm upgrade checkout ./charts/checkout \
  --atomic \
  --set rollout-guard.job.postUpgradeHook=true
```

ServiceAccount-токен в Pod не монтируется. Доступ к Prometheus берётся из уже
существующего Secret и попадает в процесс только через переменную окружения.

## Что происходит в пайплайне

| Результат | Действие |
|---|---|
| Все проверки прошли | Продолжить релиз |
| Нарушен хотя бы один порог | Откатить кандидата |
| Ошибка Prometheus, запроса, конфигурации или данных | Остановиться и разобраться |

Подробнее: [архитектура](docs/architecture.md),
[runbook](docs/runbook.md) и
[ADR о прямых запросах в Prometheus](docs/adr/0001-query-prometheus-directly.md).

## Структура репозитория

```text
src/rollout_guard/       CLI, конфигурация, клиент Prometheus и вычисление решения
tests/                   unit- и HTTP-интеграционные тесты
examples/                локальная и приближенная к production конфигурации
charts/rollout-guard/    Helm chart с Kubernetes Job
prometheus/              конфигурация Prometheus для локального стенда
docs/                    схема, runbook и принятые решения
.github/workflows/       тесты, security scan и публикация образа в GHCR
```

## Границы первой версии

Rollout Guard пока не переключает трафик, не изменяет Deployment и не хранит
историю решений. Это осознанная граница: такими операциями уже занимаются Helm,
Argo Rollouts, Flagger или сам delivery-пайплайн.

Следующие задачи вынесены в issues: сравнение кандидата со stable-версией,
сохраняемый JSON-отчёт о решении и переиспользуемый GitHub Action.
