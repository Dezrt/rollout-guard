PYTHON ?= python3

.PHONY: install test check demo docker-build helm-lint clean

install:
	$(PYTHON) -m pip install -e .

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

check: test
	$(PYTHON) -m compileall -q src tests

demo:
	docker compose up -d prometheus
	$(PYTHON) scripts/wait_for_prometheus.py http://localhost:9090/-/ready
	docker compose run --rm guard

docker-build:
	docker build --target runtime -t rollout-guard:dev .

helm-lint:
	helm lint charts/rollout-guard
	helm template rollout-guard charts/rollout-guard >/dev/null

clean:
	find . -type d -name __pycache__ -prune -exec rm -r {} +
	find . -type f -name '*.pyc' -delete

