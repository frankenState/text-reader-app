"""Print the current release version, for package.bat."""

from __future__ import annotations

import json
from pathlib import Path

conf = Path(__file__).resolve().parent.parent / "src-tauri" / "tauri.conf.json"
print(json.loads(conf.read_text(encoding="utf-8"))["version"])
