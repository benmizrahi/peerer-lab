# Security

## Reporting

Please **do not** open a public issue for security problems. Email **benmizrahi@google.com** with a description and reproduction steps. You'll get an acknowledgement within a few days.

> This is a demonstration project, not a Google product, and is **not eligible for the Google Open Source Software Vulnerability Rewards Program**. See the disclaimer in the [README](README.md).

## Threat model in one paragraph

Peerer lets another person's Antigravity start a conversation on your machine. The trust boundary is the **broker token** (shared by everyone on a broker) and, for direct LAN mode, the per-daemon **`auth_token`**. Anyone holding a valid token can send you a message that becomes an agent prompt. Treat broker tokens like you'd treat a shared Slack workspace invite: only hand them to people you'd let message you.

## What the code does to stay safe

- The daemon binds to **`127.0.0.1` only**; nothing listens on the network. All broker traffic is **outbound HTTPS** with certificate verification (`certifi` if available, system store otherwise). `PEER_INSECURE_SSL=1` exists for debugging and is never set by default.
- Inbound `/api/v1/messages` requires a bearer token compared with `hmac.compare_digest`. Payloads are capped at 2 MB; `conversation_id` is validated against `^[a-zA-Z0-9_-]+$` before being passed to `agentapi`.
- Messages are passed to `agentapi` as **argv**, never through a shell.
- The panel's settings API only exposes/accepts user-facing keys and **never returns a token in full** (set/unset + 4-char hint). `host`, `port`, and the local `auth_token` cannot be changed from the UI.
- All HTML rendered from message content is escaped; the panel has no remote script dependencies.
- Secrets live only in `config.json` / `peers.json`, both git-ignored. The installer generates a random local `auth_token` per machine.
- Desktop notifications strip quotes/newlines before being passed to `osascript` / `notify-send`.

## Known limitations

- A broker token is a shared secret per broker; there is no per-user identity or revocation yet. Rotate the token on the broker (`PEER_BROKER_TOKEN`) and redistribute if it leaks.
- Incoming messages are injected as agent prompts. Antigravity's own tool-permission settings are the last line of defense against a malicious peer asking your agent to do something destructive; keep them on.
