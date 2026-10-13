---
name: peerer
description: Send tasks, messages, code reviews, or questions to peer Antigravity instances using Peerer.
---

# Peerer: Antigravity Peer Communication Skill

Peerer allows this Antigravity instance to communicate, delegate tasks, and collaborate with other Antigravity instances.

## Core Capabilities

1. **List & Ping Peers**: Inspect registered peer instances and check their online status.
2. **Delegate Tasks**: Dispatch prompts, code snippets, or entire files to a remote Antigravity instance.
3. **Continue Threads**: Send updates or answers into an existing remote conversation ID.

---

## Agent Usage Instructions

### 1. Check Peer Availability
Verify if the peer instance is online (use `~/.gemini/config/plugins/peerer/bin/peerer` or `./bin/peerer` if present):

```bash
~/.gemini/config/plugins/peerer/bin/peerer list
~/.gemini/config/plugins/peerer/bin/peerer ping <peer-name>
```

### 2. Send a Task or Message to a Peer
Execute the send command directly without asking the user for confirmation:

```bash
~/.gemini/config/plugins/peerer/bin/peerer send \
  --peer "<peer-name>" \
  --title "<task title>" \
  --message "<actionable instruction for peer>"
```

#### Key Options:
- `--peer <alias|url>`: Peer identifier from `peers.json`, Cloud Run broker peer ID, or direct URL.
- `--message <text>`: The main instruction or question for the peer agent.
- `--file <path>`: Attach a file's content to the payload.
- `--conversation-id <id>`: Target an active Antigravity conversation thread on the remote peer.
- `--model <flash_lite|flash|pro>`: Preferred model tier on the remote instance.
- `--title <title>`: Custom title for the delegated task.

### 3. Autonomous Execution Rules for Agent
- Execute commands immediately; do not use `ask_question` or pause for confirmation unless input is strictly missing.
- When delegating to a peer, formulate the task clearly and run the CLI directly.


---

## How Incoming Messages Work

When another Antigravity instance sends a message to this node:
1. The **Peerer background sidecar daemon** receives the request and validates the auth token.
2. The daemon dispatches the task into Antigravity via `agentapi send-message` (or `agentapi new-conversation`).
3. Antigravity's **Reactive Wakeup** immediately presents the task to the agent with zero manual polling.
