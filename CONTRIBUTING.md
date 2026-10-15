# Contributing

Thanks for helping improve Peerer. This is a small project; the process is deliberately light.

## Setup

```bash
git clone git@github.com:benmizrahi/peerer-lab.git
cd peerer-lab
make install        # links peerer-plugin from this checkout into ~/.gemini/config (symlinks → edits are live)
make test
```

The plugin has **no third-party Python dependencies** (stdlib only) so it runs on any macOS/Linux Python ≥ 3.9.
The broker uses FastAPI; `make test` creates a venv for it automatically.

## Project layout

- `peerer-plugin/sidecars/peerer/daemon.py` — the always-on daemon: HTTP API, broker sync, notifications, embedded UI routes.
- `peerer-plugin/sidecars/peerer/web/index.html` — the panel. Single file, no build step, no framework.
- `peerer-plugin/skills/peerer/` — `SKILL.md` (what the agent reads) and `scripts/` (CLI logic shared with `bin/peerer`).
- `peerer-server/app/` — FastAPI broker.

## Making changes

1. Branch from `main`.
2. Keep the UI in the Antigravity style (see the CSS variables at the top of `index.html`) and minimal — one header row, chat, input.
3. Add or update tests in `peerer-plugin/tests/` or `peerer-server/tests/`. `make test` must pass.
4. Add a line under **[Unreleased]** in `CHANGELOG.md`.
5. Open a PR. CI runs both test suites.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `docs:`, …) — this is what makes release notes readable.

## Releasing

Maintainers only:

```bash
make release V=1.2.0
```

This bumps `peerer-plugin/plugin.json`, moves **[Unreleased]** to a dated `[1.2.0]` section in the changelog, commits, tags `v1.2.0`, and pushes. The **release** workflow then builds `peerer-plugin-1.2.0.zip` and publishes a GitHub Release with the changelog section as notes.

## Secrets

Never commit `config.json`, `peers.json`, or `runtime.json` (all git-ignored). If you need to show config in docs, use `config.example.json`.
