#!/usr/bin/env bash
#
# Uninstall Peerer.command
# Double-click to remove Peerer from Antigravity. Your downloaded folder is kept.
#

clear
echo
echo "  Peerer — Uninstaller"
echo
echo "  This removes Peerer from Antigravity. It will NOT delete this folder,"
echo "  so you can reinstall later by double-clicking “Install Peerer.command”."
echo
read -r -p "  Press Enter to uninstall (or close this window to cancel)… " _

pkill -f "sidecars/peerer/daemon.py" 2>/dev/null || pkill -f "daemon.py" 2>/dev/null || true
rm -rf "$HOME/.gemini/config/plugins/peerer" \
       "$HOME/.gemini/config/skills/peerer" \
       "$HOME/.gemini/config/sidecars/peerer" \
       "$HOME/.local/bin/peerer" 2>/dev/null || true

echo
echo "  ✔ Peerer has been removed. Restart Antigravity to finish."
echo
read -r -p "  Press Enter to close… " _
