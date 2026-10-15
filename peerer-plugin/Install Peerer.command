#!/usr/bin/env bash
#
# Install Peerer.command
# Double-click this file in Finder to install Peerer into Antigravity.
#
# (If macOS says the file "cannot be opened because it is from an unidentified
#  developer", right-click → Open → Open.)
#

HERE="$(cd "$(dirname "$0")" && pwd)"

clear
echo
echo "  ┌──────────────────────────────────────────────┐"
echo "  │          Peerer — Installer for Antigravity   │"
echo "  └──────────────────────────────────────────────┘"
echo
echo "  This will connect your Antigravity to other people's Antigravity"
echo "  so you can share sessions and messages. It takes about 30 seconds."
echo
read -r -p "  Press Enter to begin (or close this window to cancel)… " _

# --- Make sure Python is available (macOS offers to install it automatically) ---
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
  echo
  echo "  macOS needs to install a small developer package first."
  echo "  A window will pop up — click “Install”, wait for it to finish,"
  echo "  then double-click this file again."
  echo
  xcode-select --install >/dev/null 2>&1 || true
  read -r -p "  Press Enter to close… " _
  exit 0
fi

# --- Make sure the actual installer is next to us ---
if [[ ! -f "$HERE/install.sh" ]]; then
  echo
  echo "  ✘ Couldn't find install.sh next to this file."
  echo "    Please keep this file inside the 'peerer-plugin' folder and try again."
  echo
  read -r -p "  Press Enter to close… " _
  exit 1
fi

# --- Run the real installer (it will ask 1–3 simple questions) ---
echo
echo "  I'll ask you a couple of quick questions. Pressing Enter accepts the default."
echo
chmod +x "$HERE/install.sh"
if bash "$HERE/install.sh"; then
  echo
  echo "  ┌──────────────────────────────────────────────┐"
  echo "  │   ✔  All done! Peerer is installed.          │"
  echo "  └──────────────────────────────────────────────┘"
  echo
  echo "  What to do next:"
  echo "    1. Open Antigravity (or quit and reopen it if it was already running)."
  echo "    2. Click the puzzle-piece ‘Extensions’ icon at the top-right."
  echo "    3. Choose “Mesh & Activity” — that's your Peerer panel."
  echo
  echo "  You can close this window now."
else
  echo
  echo "  ✘ Something went wrong. Please take a screenshot of this window and"
  echo "    send it to whoever shared Peerer with you."
fi
echo
read -r -p "  Press Enter to close… " _
