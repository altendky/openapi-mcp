#!/usr/bin/env python3
"""Pack a native binary and its npm launcher without publishing anything."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64", "win32-x64"]


def native_platform():
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "win32"}[platform.system()]
    arch = {"x86_64": "x64", "AMD64": "x64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
    return system + "-" + arch


def run(binary, target, output):
    binary, output = binary.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    main = json.loads((ROOT / "npm/openapi-mcp-rs/package.json").read_text())
    expected = "openapi-mcp " + main["version"]
    actual = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if actual != expected:
        raise RuntimeError(f"expected {expected!r}, got {actual!r}")
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required")
    with tempfile.TemporaryDirectory(prefix="openapi-mcp-pack-") as temp:
        temp = Path(temp)
        wrapper, native = temp / "main", temp / "platform"
        shutil.copytree(ROOT / "npm/openapi-mcp-rs", wrapper,
                        ignore=shutil.ignore_patterns("node_modules", "coverage", "*.tgz"))
        shutil.copytree(ROOT / "npm" / target, native, ignore=shutil.ignore_patterns("bin"))
        (native / "bin").mkdir()
        name = "openapi-mcp.exe" if target.startswith("win32-") else "openapi-mcp"
        shutil.copy2(binary, native / "bin" / name)
        (native / "bin" / name).chmod(0o755)
        main["optionalDependencies"] = {"@openapi-mcp-rs/" + name: main["version"] for name in PLATFORMS}
        main["files"] = ["bin.js", "lib.js", "LICENSE-MIT", "LICENSE-APACHE"]
        (wrapper / "package.json").write_text(json.dumps(main, indent=2) + "\n", encoding="utf-8", newline="\n")
        native_manifest = json.loads((native / "package.json").read_text())
        native_manifest["files"] = ["bin", "LICENSE-MIT", "LICENSE-APACHE"]
        if native_manifest["version"] != main["version"]:
            raise RuntimeError("launcher and platform package versions differ")
        (native / "package.json").write_text(json.dumps(native_manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        for directory in [native, wrapper]:
            for name in ["LICENSE-MIT", "LICENSE-APACHE"]:
                shutil.copy2(ROOT / name, directory / name)
            packed = subprocess.check_output([
                npm, "pack", str(directory), "--pack-destination", str(output),
                "--ignore-scripts", "--offline", "--json",
            ], text=True)
            print(output / json.loads(packed)[0]["filename"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--platform", choices=PLATFORMS, default=native_platform())
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    run(args.binary, args.platform, args.output)
