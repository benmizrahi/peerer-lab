---
name: peerer
description: Send tasks, messages, code reviews, or questions to peer Antigravity instances using Peerer.
---

# Peerer: Antigravity Peer Communication Skill

Peerer allows this Antigravity instance to communicate, delegate tasks, and collaborate with other Antigravity instances.

## Opening the Peerer panel inside Antigravity

When the user asks to see/open Peerer (the panel, the mesh, peers, shared sessions), emit this pill —
it opens the panel in Antigravity's side pane. **Never** open the daemon URL in a browser instead.

```markdown
[Open Peerer panel](sidecar://peerer/peerer/)
```

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

## Collaboration Threads (working together)

A **thread** binds your current conversation to a peer's conversation. Anything sent on a thread
lands *inside* the other person's open conversation (and their replies land here) — so two users'
agents can keep working on the same thing without copy-pasting.

Incoming peer messages that belong to a thread end with a block like:

```
--- Collaboration thread t_1a2b3c4d with Bob Example ---
  Reply:            peerer reply --thread t_1a2b3c4d "your message"
  Reply with files: peerer reply --thread t_1a2b3c4d --attach path/to/file "message"
```

**When the user asks you to answer, ask, or hand something to that peer, use exactly that command.**
Keep replies concise and self-contained; attach files rather than pasting large content.

```bash
peerer threads                                  # list threads (id, peer, unread, bound conversations)
peerer reply --thread t_1a2b3c4d "Done — tests pass, see attached"  --attach tests/test_x.py
```

### Shared sessions with code
A shared session folder (`~/Peerer Shared/<Sender>/<title>/`) may contain `repo.json`,
`changes.patch`, `commits.patch` and `untracked/`. The user can apply them with **Apply to my
checkout** in the Peerer panel (creates a `peerer/<thread>` branch). If asked to do it yourself:
`git checkout -b peerer/review && git apply --3way changes.patch` in the matching checkout.
