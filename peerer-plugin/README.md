# Peerer Plugin

The Antigravity plugin: an always-on sidecar daemon with an embedded **Mesh & Activity** panel, a skill so the agent can message peers, and the `peerer` CLI.

> Project overview, architecture, and broker docs live in the [repository README](../README.md).
> Non-technical install guide: [INSTALL.md](./INSTALL.md) · Changes: [CHANGELOG](../CHANGELOG.md)

---

## Directory Structure

```text
peerer-plugin/
├── plugin.json                 # Manifest — single source of truth for the version
├── install.sh                  # One-line installer / upgrader
├── Install Peerer.command      # Double-click installer (macOS)
├── Uninstall Peerer.command
├── INSTALL.md                  # Plain-language guide
├── bin/
│   └── peerer                  # CLI: send · list · ping · add · remove · inbox · health · daemon · --version
├── sidecars/peerer/
│   ├── sidecar.json            # Sidecar registration + web UI view (aux pane)
│   ├── daemon.py               # HTTP API, broker sync, notifications, settings, UI routes
│   ├── web/index.html          # The panel (single file, no build step)
│   ├── config.example.json     # Template; real config.json is git-ignored
│   └── runtime.json            # (generated) host/port/pid of the running daemon
├── skills/peerer/
│   ├── SKILL.md                # Instructions the agent reads
│   └── scripts/
│       ├── send.py             # Outbound messaging (direct or via broker)
│       ├── peers.py            # Peer registry + ping
│       └── peers.example.json
└── tests/
    ├── test_bridge.py          # Daemon API, auth, UI endpoints, settings
    └── test_cli.py             # CLI integration
```

---

## Getting Started

> **Not a developer?** See [INSTALL.md](./INSTALL.md) — you can ask Antigravity
> to install it for you, or just double-click **`Install Peerer.command`**.

### 0. Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/benmizrahi/peerer-lab/main/peerer-plugin/install.sh | bash
```

The installer clones the repo to `~/.peerer`, prompts for an instance name and
(optional) broker URL/token, generates a `config.json` with a random auth token,
links the plugin + skill + `peerer` CLI into Antigravity, and starts the sidecar.
Re-running it upgrades in place and **never overwrites an existing `config.json`**.

Non-interactive / scripted install:

```bash
PEERER_NONINTERACTIVE=1 \
PEERER_INSTANCE_NAME=my-laptop \
PEERER_BROKER_URL=https://your-broker.a.run.app \
PEERER_BROKER_TOKEN=... \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/benmizrahi/peerer-lab/main/peerer-plugin/install.sh)"
```

From a local checkout: `./install.sh`

**Upgrade:** `git -C ~/.peerer pull && ~/.peerer/peerer-plugin/install.sh`
**Uninstall:** `rm -rf ~/.gemini/config/plugins/peerer ~/.gemini/config/skills/peerer ~/.local/bin/peerer ~/.peerer`

### 1. Configure Peerer

**In the panel:** click the ⚙️ icon (top-right of *Mesh & Activity*) to set:

- **Display name** — pre-filled from your Antigravity Google account / macOS profile / git identity. Your peer ID is derived from it (`Ben Mizrahi` → `ben-mizrahi`).
- **Server URL + access token** — the broker other peers connect through. *Test connection* checks it before you save. Leave blank for local-network-only mode.
- **Desktop notifications** on/off.

Changes are saved to `sidecars/peerer/config.json` and applied immediately (the daemon re-registers with the server — no restart needed). The panel opens Settings automatically on first run if no server is configured.

**Or edit the file directly:**
```json
{
  "display_name": "Ben Mizrahi",
  "instance_name": "ben-mizrahi",
  "host": "127.0.0.1",
  "port": 8950,
  "auth_token": "…",
  "inbox_limit": 50,
  "broker_url": "https://your-broker.a.run.app",
  "broker_token": "…",
  "notifications": true
}
```

Optional environment variables:
- `COMM_BRIDGE_HOST`: Bind address (default `127.0.0.1`)
- `COMM_BRIDGE_PORT` / `ANTIGRAVITY_SIDECAR_WEB_PORT`: Port (default `8950`)
- `PEER_AUTH_TOKEN`: Bearer token override

### 2. Running the Peerer Listener
When loaded as an Antigravity plugin, the sidecar is managed automatically via `sidecar.json`.

To run or test it standalone via the CLI:
```bash
./bin/peerer daemon
```

### 3. Pinging and Managing Peers
```bash
# Check connectivity to a peer
./bin/peerer ping local-node

# List all registered peers
./bin/peerer list

# Register a new peer
./bin/peerer add workstation "http://192.168.1.50:8950" --token "secret"
```

### 4. Sending Messages and Delegating Tasks
```bash
# Start a new conversation task on the remote peer
./bin/peerer send \
  --peer "local-node" \
  --title "Security Review" \
  --message "Please audit this function for SQL injection risks." \
  --file "src/db.py"

# Or send directly into an active conversation thread on the remote peer
./bin/peerer send \
  --peer "local-node" \
  --conversation-id "312c12c3-d9fb-4f9b-a9d5-628a4b68b581" \
  --message "Here are the benchmark results you requested."
```

### 5. Inspecting Inbound Messages
```bash
./bin/peerer inbox
```

---

## Embedded Sidecar Web UI

Peerer comes with an integrated Web UI dashboard running directly inside Antigravity's Auxiliary Pane:

- **Live Peer Mesh View**: Visual topology of connected peers, heartbeat countdowns, and ping latency.
- **Activity & Task Stream**: Real-time bidirectional log of incoming tasks dispatched to `agentapi` and outgoing messages.
- **Task Delegation Console**: Directly select an active peer, customize title, prompt, and model tier (`flash`, `pro`, `inherit`), and dispatch tasks with one click.
- **Configuration**: Managed in `sidecars/peerer/sidecar.json` (`has_web_ui: true`, `ui_config` entrypoint set to `SIDECAR_UI_ENTRYPOINT_AUX_PANE`).

---

## Running Tests

```bash
# Run all unit & integration tests
python3 -m unittest discover tests
```
