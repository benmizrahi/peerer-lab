#!/usr/bin/env python3
"""
Bump the project version.

  python3 scripts/bump_version.py 1.2.0

- Sets `version` in peerer-plugin/plugin.json (single source of truth)
- Moves the CHANGELOG "[Unreleased]" section into a dated "[1.2.0]" section
  and refreshes the compare links at the bottom.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JSON = ROOT / "peerer-plugin" / "plugin.json"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO = "https://github.com/benmizrahi/peerer-lab"


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print(__doc__)
        return 2
    new = sys.argv[1]

    data = json.loads(PLUGIN_JSON.read_text())
    old = data.get("version", "0.0.0")
    if new == old:
        print(f"version is already {new}")
        return 1
    data["version"] = new
    PLUGIN_JSON.write_text(json.dumps(data, indent=2) + "\n")

    text = CHANGELOG.read_text()
    today = date.today().isoformat()

    # Split out the Unreleased body
    m = re.search(r"## \[Unreleased\]\n(.*?)(?=\n## \[|\n\[Unreleased\]:)", text, re.S)
    body = (m.group(1).strip("\n") if m else "").strip()
    if not body:
        body = "### Changed\n- Maintenance release."

    text = text.replace(
        "## [Unreleased]\n" + (m.group(1) if m else ""),
        f"## [Unreleased]\n\n## [{new}] - {today}\n\n{body}\n",
        1,
    )

    # Rewrite link refs
    text = re.sub(r"\[Unreleased\]: .*", f"[Unreleased]: {REPO}/compare/v{new}...HEAD", text)
    if f"[{new}]:" not in text:
        text = text.rstrip("\n") + f"\n[{new}]: {REPO}/compare/v{old}...v{new}\n"
    CHANGELOG.write_text(text)

    print(f"{old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
