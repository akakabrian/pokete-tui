ENGINE := engine

.PHONY: all bootstrap venv run clean test test-only test-api test-perf playtest smoke

all: bootstrap venv

bootstrap: $(ENGINE)/.git
$(ENGINE)/.git:
	@echo "==> cloning Pokete into engine/ (one time)"
	git clone --depth=1 https://github.com/lxgr-linux/pokete.git $(ENGINE)

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .
	.venv/bin/pip install -e $(ENGINE)

run: venv
	.venv/bin/python run_pokete.py

smoke: venv
	.venv/bin/python -m tests.smoke_engine

# Full QA suite — TUI scenarios + agent API + perf bench.
test: venv
	.venv/bin/python -m tests.qa
	.venv/bin/python -m tests.api_qa
	.venv/bin/python -m tests.perf

test-only: venv
	.venv/bin/python -m tests.qa $(PAT)

test-api: venv
	.venv/bin/python -m tests.api_qa

test-perf: venv
	.venv/bin/python -m tests.perf

playtest: venv
	.venv/bin/python -m tests.playtest

clean:
	rm -rf .venv
