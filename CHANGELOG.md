# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added
- **Collaboration threads.** A thread binds your conversation to a peer's conversation. Messages on a thread are delivered with `agentapi send-message` *into the bound conversation* on each side instead of spawning new ones — both people (and both agents) keep working where they already are. Threads are created automatically on first message/share and persisted in `threads.json`.
- **Agents can reply to each other.** Incoming thread messages end with `peerer reply --thread <id> "…" [--attach file]`; the receiving agent uses it to answer, ask, or hand files back. `peerer threads` lists threads. Attachments land in `~/Peerer Shared/<Sender>/attachments/`.
- **Code travels with sessions.** Sharing a session from a git workspace also ships `repo.json` (origin/branch/HEAD), `changes.patch` (uncommitted work), `untracked/` (new files) and `commits.patch` (unpushed commits). The receiver gets **Apply to my checkout**: finds (or clones) the same repo, creates a `peerer/<thread>-<sha>` branch, applies everything, opens the folder, and tells the agent to review. Dirty checkouts are refused, never overwritten.
- Panel: thread chips on messages (click = reply in that thread), reply bar, *Reply* / *Apply to my checkout* buttons on session cards, code summary box, "include my code changes" toggle in the share dialog.
- Endpoints: `POST /api/v1/threads/reply`, `POST /api/v1/threads/read`, `POST /api/v1/ui/apply-patch`; `threads` in `/ui/state`.

## [1.1.2] - 2026-10-15

### Fixed
- **Clear chat did nothing**: the Antigravity webview swallows `confirm()` dialogs. Clearing is now a two-click inline confirmation; all `alert/confirm/prompt` dialogs were removed from the panel in favour of toasts.
- **Open with Antigravity now always lands somewhere**: when Antigravity refuses to start a new conversation (`project_id is required…`, no project window active), the session prompt is delivered into the conversation the panel is open in instead. Same fallback for inbound messages. `agentapi` is now run without `check=True` so a non-zero exit can't bypass the fallback.

## [1.1.1] - 2026-10-15

### Fixed
- *Open with Antigravity* silently did nothing when Antigravity refused to start a conversation (e.g. no project window active). `agentapi` exits 0 with the error inside its JSON; the daemon now detects that, the panel shows the reason, and a new **Copy prompt** button offers a fallback you can paste into any chat.

## [1.1.0] - 2026-10-15

### Added
- **Real session sharing.** *Share Session* now sends a portable copy of a conversation — its artifacts, uploaded files, and a readable transcript digest (`SESSION.md`) — packed into a size-budgeted zip inside the peer message. The receiver gets it under `~/Peerer Shared/<Sender>/<title>/`, a card in the panel listing the files, **Open with Antigravity** (starts a conversation whose agent reads the session) and **Show files**. Only what the sender explicitly shares ever leaves a machine.
- Session picker in the share dialog (recent sessions, current one preselected, artifact count/size, optional note, transcript on/off).
- **Clear chat** button (🗑) in the panel header; received session files are kept.
- Endpoints: `GET /api/v1/ui/sessions`, `POST /api/v1/ui/share-session`, `POST /api/v1/ui/open`, `POST /api/v1/ui/open-agent` (the last two are restricted to `~/Peerer Shared`).

### Fixed
- Fresh installs without a server: onboarding is now driven by daemon state (no browser storage) — the panel shows a "Not connected" banner, a *Set up server* call-to-action, and opens Settings automatically.
- Broker/identity failures can no longer prevent the panel from starting (daemon always serves the UI; falls back to local-only).
- Installer runs a daemon self-test (`daemon.py --check`) and prints the real error if the panel wouldn't come up.
- Panel showed your own machine (`local-node`, a loopback entry in `peers.json`) as a peer; loopback entries are now excluded.
- Peers, message senders/targets, toasts, and the agent prompt header now show display names (e.g. "Ben Mizrahi") instead of peer IDs. Outgoing messages carry `metadata.sender_name`.

## [1.0.0] - 2026-10-15

First public release.

### Added
- **Plugin** for Antigravity: always-on sidecar daemon, agent skill, and `peerer` CLI.
- **Mesh & Activity panel** (embedded sidecar web UI, aux pane) styled to Antigravity:
  presence list of online peers, chat, *Share Session*, *Share Data*, unread badge and toasts.
- **Settings view** in the panel: display name (auto-detected from Antigravity account / macOS / git),
  server URL + token with *Test connection*, desktop notifications toggle. Saved settings hot-apply
  and re-register with the broker without a restart.
- **Native desktop notifications** on inbound messages (macOS Notification Center / `notify-send`).
- **Installers**: one-line `curl | bash`, double-clickable `Install Peerer.command` / `Uninstall Peerer.command`,
  and a plain-language `INSTALL.md` for non-technical users.
- **Cloud Run broker** (`peerer-server`): peer registry, heartbeats, long-poll message queue, ack/receipts,
  bearer-token auth, `deploy.sh` + Cloud Build config.
- CLI: `peerer send | list | ping | add | remove | inbox | health | daemon | --version`.
- `runtime.json` so the CLI finds the daemon on the dynamic sidecar port.
- CI (GitHub Actions) running plugin and broker test suites; release workflow on `v*` tags.

### Security
- Broker and peer tokens are stored only in the git-ignored `config.json`; the settings API never returns a token in full.
- Settings endpoint accepts only user-facing keys (`display_name`, `instance_name`, `broker_url`, `broker_token`, `notifications`).

[Unreleased]: https://github.com/benmizrahi/peerer-lab/compare/v1.1.2...HEAD
[1.0.0]: https://github.com/benmizrahi/peerer-lab/releases/tag/v1.0.0
[1.1.0]: https://github.com/benmizrahi/peerer-lab/compare/v1.0.0...v1.1.0
[1.1.1]: https://github.com/benmizrahi/peerer-lab/compare/v1.1.0...v1.1.1
[1.1.2]: https://github.com/benmizrahi/peerer-lab/compare/v1.1.1...v1.1.2
