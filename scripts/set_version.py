"""Set the release version in the manifests that carry one.

    python scripts/set_version.py 1.2.3

Kept as a script so package.bat has one place to change the version and the
three files can never drift apart.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI_CONF = ROOT / "src-tauri" / "tauri.conf.json"
CARGO_TOML = ROOT / "src-tauri" / "Cargo.toml"
PACKAGE_JSON = ROOT / "package.json"


def fail(message: str) -> None:
    print(f"set_version: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_current() -> str:
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))["version"]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        fail("usage: set_version.py X.Y.Z")

    version = argv[1].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"'{version}' is not a X.X.Z style version")

    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    previous = conf.get("version", "0.0.0")
    conf["version"] = version
    TAURI_CONF.write_text(json.dumps(conf, indent=2) + "\n", encoding="utf-8")

    # Only the first version key belongs to [package]; the rest are deps.
    cargo = CARGO_TOML.read_text(encoding="utf-8")
    replaced, count = re.subn(
        r'(?m)^version = "[^"]*"', f'version = "{version}"', cargo, count=1
    )
    if count != 1:
        fail("could not find the package version in Cargo.toml")
    CARGO_TOML.write_text(replaced, encoding="utf-8")

    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    package["version"] = version
    PACKAGE_JSON.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")

    print(f"version {previous} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
