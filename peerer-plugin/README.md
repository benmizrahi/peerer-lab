# Peerer: Antigravity Peer Bridge

A native peer-to-peer communication bridge connecting **Google Antigravity instances** using **Sidecars** and **`agentapi`**.

This extension operates directly on Antigravity's native primitives—no third-party frameworks or A2A protocols required.

---

## Architecture Overview

```
+-------------------------------------------------------------+
|               Antigravity Instance A                        |
|                                                             |
|  [Agent Turn]                                               |
|         │                                                   |
|         ▼                                                   |
|  [Peerer Skill]                                             |
|         │                                                   |
|         ▼                                                   |
|  peerer send --peer http://instance-b:8950                  |
+─────────┬───────────────────────────────────────────────────+
          │
          │ HTTP POST /api/v1/messages (Bearer Auth)
          ▼
+─────────────────────────────────────────────────────────────+
|               Antigravity Instance B                        |
|                                                             |
|  [Sidecar: daemon.py (Port 8950)]                           |
|         │                                                   |
|         │ 1. Validate Token & Payload                       |
|         │ 2. Record in Local Inbox                          |
|         ▼                                                   |
|  agentapi send-message (or new-conversation)                |
|         │                                                   |
|         ▼                                                   |
|  [Reactive Wakeup: Agent B executes delegated task]         |
+-------------------------------------------------------------+
```

---

## Directory Structure

```text
peerer/
├── bin/
│   └── peerer                  # Unified executable CLI (ping, list, send, daemon, inbox)
├── plugin.json                 # Antigravity plugin manifest
├── README.md                   # Documentation and usage guide
├── sidecars/
│   └── peerer/
│       ├── sidecar.json        # Sidecar daemon registration
│       ├── config.json         # Bridge configuration (port, token, limits)
│       └── daemon.py           # Inbound HTTP daemon bridging to agentapi
├── skills/
│   └── peerer/
│       ├── SKILL.md            # Agent instructions for delegating peer tasks
│       └── scripts/
│           ├── peers.json      # Known peers registry
│           ├── peers.py        # Peer management tool
│           └── send.py         # Outbound messaging tool
└── tests/
    ├── test_bridge.py          # Unit tests for daemon and auth
    └── test_cli.py             # Integration tests for peerer CLI
```

---

## Getting Started

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
Edit `sidecars/peerer/config.json`:
```json
{
  "instance_name": "peerer-local",
  "host": "127.0.0.1",
  "port": 8950,
  "auth_token": "antigravity-peer-secret-token",
  "inbox_limit": 50
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
