.PHONY: setup services test-services run test lint browser
setup:
	bash scripts/setup.sh
services:
	.venv/bin/python scripts/dev_services.py
test-services:
	.venv/bin/python scripts/dev_services.py --test-db
run:
	bash scripts/start.sh
test:
	.venv/bin/pytest -q
lint:
	.venv/bin/ruff check kbagent tests scripts
	.venv/bin/ruff format --check kbagent tests scripts
	node --check kbagent/static/app.js
	node --check kbagent/static/wiki.js
	node --check kbagent/static/repositories.js
browser:
	.venv/bin/python scripts/browser_smoke.py
