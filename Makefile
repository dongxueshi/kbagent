.PHONY: setup run test lint browser
setup:
	bash scripts/setup.sh
run:
	bash scripts/start.sh
test:
	.venv/bin/pytest -q
lint:
	.venv/bin/ruff check kbagent tests scripts
	.venv/bin/ruff format --check kbagent tests scripts
	node --check kbagent/static/app.js
	node --check kbagent/static/wiki.js
browser:
	.venv/bin/python scripts/browser_smoke.py
