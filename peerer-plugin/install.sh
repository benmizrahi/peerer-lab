#!/usr/bin/env bash
#
# Peerer — One-line installer for Antigravity
#
#   curl -fsSL https://raw.githubusercontent.com/benmizrahi/peerer-lab/main/peerer-plugin/install.sh | bash
#
# Or from a local checkout:
#   ./install.sh
#
# Options (env vars):
#   PEERER_INSTANCE_NAME   Name shown to other peers       (default: <hostname>)
#   PEERER_BROKER_URL      Cloud Run broker URL            (default: prompt / none)
#   PEERER_BROKER_TOKEN    Broker shared secret            (default: prompt / none)
#   PEERER_HOME            Where to clone the repo         (default: ~/.peerer)
#   PEERER_NONINTERACTIVE  Set to 1 to skip prompts
#

set -euo pipefail

REPO_URL="https://github.com/benmizrahi/peerer-lab.git"
PEERER_HOME="${PEERER_HOME:-$HOME/.peerer}"
CONFIG_DIR="$HOME/.gemini/config"
BIN_DIR="$HOME/.local/bin"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
ok()    { printf "  \033[32m✔\033[0m %s\n" "$*"; }
info()  { printf "  \033[34m→\033[0m %s\n" "$*"; }
warn()  { printf "  \033[33m!\033[0m %s\n" "$*"; }
fail()  { printf "  \033[31m✘\033[0m %s\n" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
bold "Peerer installer"
command -v python3 >/dev/null 2>&1 || fail "python3 is required (3.9+)."
command -v git     >/dev/null 2>&1 || fail "git is required."

PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 9)))')
[[ "$PY_OK" == "1" ]] || fail "Python 3.9+ required (found $(python3 --version))."
ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2), git available"

# ---------------------------------------------------------------------------
# 1. Resolve source: local checkout (script inside repo) or clone/update
# ---------------------------------------------------------------------------
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_SRC" && -f "$SCRIPT_SRC" && -f "$(dirname "$SCRIPT_SRC")/plugin.json" ]]; then
  PLUGIN_DIR="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  info "Using local checkout: $PLUGIN_DIR"
else
  if [[ -d "$PEERER_HOME/.git" ]]; then
    info "Updating existing install at $PEERER_HOME"
    git -C "$PEERER_HOME" pull --ff-only --quiet
  else
    info "Cloning $REPO_URL → $PEERER_HOME"
    git clone --quiet --depth 1 "$REPO_URL" "$PEERER_HOME"
  fi
  PLUGIN_DIR="$PEERER_HOME/peerer-plugin"
fi
[[ -f "$PLUGIN_DIR/plugin.json" ]] || fail "plugin.json not found in $PLUGIN_DIR"
ok "Source ready"

# ---------------------------------------------------------------------------
# 2. Configuration (only created if missing — never overwrites user secrets)
# ---------------------------------------------------------------------------
CONFIG_FILE="$PLUGIN_DIR/sidecars/peerer/config.json"
if [[ ! -f "$CONFIG_FILE" ]]; then
  INSTANCE="${PEERER_INSTANCE_NAME:-$(hostname -s 2>/dev/null || hostname)}"
  BROKER_URL="${PEERER_BROKER_URL:-}"
  BROKER_TOKEN="${PEERER_BROKER_TOKEN:-}"

  if [[ "${PEERER_NONINTERACTIVE:-0}" != "1" && -t 0 ]]; then
    echo
    bold "Configure Peerer"
    read -r -p "  Instance name [$INSTANCE]: " _in;  INSTANCE="${_in:-$INSTANCE}"
    read -r -p "  Broker URL (blank = local-only): " _in; BROKER_URL="${_in:-$BROKER_URL}"
    if [[ -n "$BROKER_URL" ]]; then
      read -r -s -p "  Broker token: " _in; echo; BROKER_TOKEN="${_in:-$BROKER_TOKEN}"
    fi
  fi

  AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  python3 - "$CONFIG_FILE" "$INSTANCE" "$AUTH_TOKEN" "$BROKER_URL" "$BROKER_TOKEN" <<'PY'
import json, sys
path, instance, auth, burl, btok = sys.argv[1:]
cfg = {
    "instance_name": instance,
    "host": "127.0.0.1",
    "port": 8950,
    "auth_token": auth,
    "inbox_limit": 50,
    "broker_url": burl.rstrip("/"),
    "broker_token": btok,
    "notifications": True,
}
json.dump(cfg, open(path, "w"), indent=2)
PY
  ok "Created config.json (instance: $INSTANCE)"
else
  ok "Keeping existing config.json"
fi

# ---------------------------------------------------------------------------
# 3. Install into Antigravity (symlinks so `git pull` upgrades in place)
# ---------------------------------------------------------------------------
mkdir -p "$CONFIG_DIR/plugins" "$CONFIG_DIR/skills" "$BIN_DIR"
chmod +x "$PLUGIN_DIR/bin/peerer" "$PLUGIN_DIR/sidecars/peerer/daemon.py"

rm -rf "$CONFIG_DIR/plugins/peerer" "$CONFIG_DIR/skills/peerer" "$CONFIG_DIR/sidecars/peerer" 2>/dev/null || true
ln -sfn "$PLUGIN_DIR"                "$CONFIG_DIR/plugins/peerer"
ln -sfn "$PLUGIN_DIR/skills/peerer"  "$CONFIG_DIR/skills/peerer"
ln -sfn "$PLUGIN_DIR/bin/peerer"     "$BIN_DIR/peerer"
ok "Linked plugin, skill, and CLI"

# ---------------------------------------------------------------------------
# 4. (Re)start the sidecar daemon
# ---------------------------------------------------------------------------
pkill -f "sidecars/peerer/daemon.py" 2>/dev/null || pkill -f "daemon.py" 2>/dev/null || true
touch "$PLUGIN_DIR/sidecars/peerer/sidecar.json"
ok "Sidecar reloaded"

# ---------------------------------------------------------------------------
# 5. Done
# ---------------------------------------------------------------------------
echo
bold "Peerer installed"
echo "  Plugin : $CONFIG_DIR/plugins/peerer"
echo "  CLI    : $BIN_DIR/peerer"
echo "  Config : $CONFIG_FILE"
echo
echo "  Next steps:"
echo "    • Reload Antigravity (⌘⇧P → “Reload Window”) if the panel doesn't appear"
echo "    • Open the “Mesh & Activity” panel from the Extensions menu"
echo "    • Verify:  peerer health   |   peerer list"
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  warn "$BIN_DIR is not on your PATH. Add:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
