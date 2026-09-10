#!/usr/bin/env python3
"""Check workspace and npm versions before building distribution artifacts."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
manifest = (ROOT / "Cargo.toml").read_text()
version = re.search(r'(?m)^version = "([^"]+)"$', manifest)[1]
for name in ["openapi-mcp-spec", "openapi-mcp-core", "openapi-mcp-io"]:
    actual = re.search(rf'(?m)^{name} = .*version = "([^"]+)"', manifest)[1]
    if actual != version:
        raise SystemExit(f"{name}: expected {version}, found {actual}")
for path in (ROOT / "npm").glob("*/package.json"):
    actual = json.loads(path.read_text())["version"]
    if actual != version:
        raise SystemExit(f"{path}: expected {version}, found {actual}")
lock = json.loads((ROOT / "npm/openapi-mcp-rs/package-lock.json").read_text())
if lock["version"] != version or lock["packages"][""]["version"] != version:
    raise SystemExit("npm lockfile version differs from workspace version")
print(f"workspace and npm versions match: {version}")
