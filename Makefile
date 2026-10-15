.PHONY: help test test-plugin test-server install uninstall release version clean

PLUGIN      := peerer-plugin
SERVER      := peerer-server
VERSION     := $(shell python3 -c "import json;print(json.load(open('$(PLUGIN)/plugin.json'))['version'])")
SERVER_VENV := $(SERVER)/.venv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

version: ## Print current version
	@echo $(VERSION)

test: test-plugin test-server ## Run all tests

test-plugin: ## Run plugin tests (stdlib only)
	python3 -m unittest discover -s $(PLUGIN)/tests -v 2>&1 | grep -vE '^127\.0\.0\.1|comm-bridge'

$(SERVER_VENV)/bin/pytest: $(SERVER)/requirements.txt
	python3 -m venv $(SERVER_VENV)
	$(SERVER_VENV)/bin/pip install -q --upgrade pip
	$(SERVER_VENV)/bin/pip install -q -r $(SERVER)/requirements.txt pytest httpx

test-server: $(SERVER_VENV)/bin/pytest ## Run broker tests (creates venv on first run)
	cd $(SERVER) && .venv/bin/pytest -q

install: ## Link the plugin from this checkout into Antigravity
	./$(PLUGIN)/install.sh

uninstall: ## Remove the plugin links from Antigravity
	rm -rf ~/.gemini/config/plugins/peerer ~/.gemini/config/skills/peerer ~/.gemini/config/sidecars/peerer ~/.local/bin/peerer
	@echo "Removed. Restart Antigravity."

release: ## Cut a release: make release V=1.2.0
	@[ -n "$(V)" ] || (echo "usage: make release V=X.Y.Z" && exit 1)
	@echo "$(V)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || (echo "V must be X.Y.Z" && exit 1)
	@[ -z "$$(git status --porcelain)" ] || (echo "working tree not clean" && exit 1)
	@git tag -l | grep -qx "v$(V)" && echo "tag v$(V) exists" && exit 1 || true
	@$(MAKE) --no-print-directory test
	python3 scripts/bump_version.py $(V)
	git add $(PLUGIN)/plugin.json CHANGELOG.md
	git commit -q -m "chore(release): v$(V)"
	git tag -a "v$(V)" -m "Peerer v$(V)"
	git push -q origin main "v$(V)"
	@echo "Pushed v$(V). GitHub Actions will publish the release."

clean: ## Remove caches and venvs
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(SERVER_VENV) dist
