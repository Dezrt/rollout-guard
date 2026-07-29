FROM python:3.12.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN python -m venv /opt/rollout-guard
COPY pyproject.toml README.md ./
COPY src ./src
RUN /opt/rollout-guard/bin/pip install .


FROM python:3.12.13-slim-bookworm AS runtime

ARG UID=10001
ARG GID=10001

ENV PATH="/opt/rollout-guard/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid "${GID}" rollout-guard \
    && useradd \
        --uid "${UID}" \
        --gid "${GID}" \
        --no-create-home \
        --shell /usr/sbin/nologin \
        rollout-guard

COPY --from=builder /opt/rollout-guard /opt/rollout-guard

USER rollout-guard
ENTRYPOINT ["rollout-guard"]
CMD ["--help"]

