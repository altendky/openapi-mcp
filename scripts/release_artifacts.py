#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Package native archives and assemble a validated, checksummed release bundle."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux-x64": "x86_64-unknown-linux-musl",
    "linux-arm64": "aarch64-unknown-linux-musl",
    "darwin-x64": "x86_64-apple-darwin",
    "darwin-arm64": "aarch64-apple-darwin",
    "win32-x64": "x86_64-pc-windows-msvc",
}
CRATES = ("openapi-mcp-spec", "openapi-mcp-core", "openapi-mcp-io", "openapi-mcp")
LICENSES = ("LICENSE-MIT", "LICENSE-APACHE")


def version():
    return tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["package"]["version"]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_name(platform, release_version):
    suffix = ".zip" if platform == "win32-x64" else ".tar.gz"
    return f"openapi-mcp-{release_version}-{TARGETS[platform]}{suffix}"


def files_in_archive(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    with tarfile.open(path) as archive:
        return {entry.name: archive.extractfile(entry).read() for entry in archive if entry.isfile()}


def pack(binary, platform, output):
    release_version = version()
    binary = binary.resolve()
    actual = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if actual != f"openapi-mcp {release_version}":
        raise ValueError(f"binary version mismatch: {actual}")
    output.mkdir(parents=True, exist_ok=True)
    name = archive_name(platform, release_version)
    stem = name.removesuffix(".zip").removesuffix(".tar.gz")
    with tempfile.TemporaryDirectory(prefix="openapi-mcp-release-") as temp:
        directory = Path(temp) / stem
        directory.mkdir()
        executable = directory / ("openapi-mcp.exe" if platform == "win32-x64" else "openapi-mcp")
        shutil.copyfile(binary, executable)
        executable.chmod(0o755)
        for license_file in LICENSES:
            shutil.copyfile(ROOT / license_file, directory / license_file)
        if platform == "win32-x64":
            with zipfile.ZipFile(output / name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(directory.iterdir()):
                    entry = zipfile.ZipInfo(f"{stem}/{path.name}")
                    entry.create_system = 3
                    entry.external_attr = (0o100755 if path == executable else 0o100644) << 16
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(entry, path.read_bytes())
        else:
            with (output / name).open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for path in sorted(directory.iterdir()):
                        entry = tarfile.TarInfo(f"{stem}/{path.name}")
                        entry.size = path.stat().st_size
                        entry.mode = 0o755 if path == executable else 0o644
                        with path.open("rb") as source:
                            archive.addfile(entry, source)
    print(output / name)


def npm_manifest(path, expected_name, release_version):
    files = files_in_archive(path)
    manifest = json.loads(files["package/package.json"])
    if manifest["name"] != expected_name or manifest["version"] != release_version:
        raise ValueError(f"unexpected package identity in {path}")
    for license_file in LICENSES:
        if files[f"package/{license_file}"] != (ROOT / license_file).read_bytes():
            raise ValueError(f"incorrect license in {path}")
    return manifest, files


def bundle(inputs, output):
    release_version = version()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    wrapper_name = f"openapi-mcp-rs-{release_version}.tgz"
    optional = {f"@openapi-mcp-rs/{platform}": release_version for platform in TARGETS}
    for platform in TARGETS:
        npm_dir = inputs / f"npm-{platform}"
        native_name = f"openapi-mcp-rs-{platform}-{release_version}.tgz"
        if {path.name for path in npm_dir.glob("*.tgz")} != {wrapper_name, native_name}:
            raise ValueError(f"missing or unexpected npm tarballs for {platform}")
        wrapper = npm_dir / wrapper_name
        manifest, _ = npm_manifest(wrapper, "openapi-mcp-rs", release_version)
        if manifest.get("optionalDependencies") != optional:
            raise ValueError("launcher must depend on every platform at the exact release version")
        if (output / wrapper_name).exists():
            if sha256(wrapper) != sha256(output / wrapper_name):
                raise ValueError("launcher tarballs differ between platforms")
        else:
            shutil.copyfile(wrapper, output / wrapper_name)
        native = npm_dir / native_name
        manifest, files = npm_manifest(native, f"@openapi-mcp-rs/{platform}", release_version)
        os_name, cpu = platform.split("-")
        if manifest.get("os") != [os_name] or manifest.get("cpu") != [cpu]:
            raise ValueError(f"incorrect platform constraints: {platform}")
        binary = "openapi-mcp.exe" if platform == "win32-x64" else "openapi-mcp"
        archive = inputs / f"release-{platform}" / archive_name(platform, release_version)
        contents = files_in_archive(archive)
        stem = archive.name.removesuffix(".zip").removesuffix(".tar.gz")
        expected = {f"{stem}/{name}" for name in (*LICENSES, binary)}
        if set(contents) != expected or contents[f"{stem}/{binary}"] != files[f"package/bin/{binary}"]:
            raise ValueError(f"native and npm binaries differ for {platform}")
        for license_file in LICENSES:
            if contents[f"{stem}/{license_file}"] != (ROOT / license_file).read_bytes():
                raise ValueError(f"incorrect native license: {platform}")
        shutil.copyfile(native, output / native.name)
        shutil.copyfile(archive, output / archive.name)
    for crate in CRATES:
        path = inputs / "crates" / f"{crate}-{release_version}.crate"
        contents = files_in_archive(path)
        manifest = tomllib.loads(contents[f"{crate}-{release_version}/Cargo.toml"].decode())
        if manifest["package"]["name"] != crate or manifest["package"]["version"] != release_version:
            raise ValueError(f"unexpected crate identity: {crate}")
        shutil.copyfile(path, output / path.name)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    hashes = {path.name: sha256(path) for path in sorted(output.iterdir())}
    (output / "release.json").write_text(json.dumps({
        "version": release_version, "commit": source, "sha256": hashes,
    }, indent=2) + "\n")
    hashes["release.json"] = sha256(output / "release.json")
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())))
    verify(output)
    print(f"Validated {len(hashes) - 1} packages/archives for {release_version} ({source})")


def verify(directory):
    manifest = json.loads((directory / "release.json").read_text())
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if manifest["version"] != version() or manifest["commit"] != source:
        raise ValueError("release bundle does not match the checked-out version and commit")
    expected_names = {archive_name(platform, version()) for platform in TARGETS}
    expected_names.update(f"openapi-mcp-rs-{platform}-{version()}.tgz" for platform in TARGETS)
    expected_names.add(f"openapi-mcp-rs-{version()}.tgz")
    expected_names.update(f"{crate}-{version()}.crate" for crate in CRATES)
    if set(manifest["sha256"]) != expected_names:
        raise ValueError("release bundle has missing or unexpected artifacts")
    if {path.name for path in directory.iterdir()} != expected_names | {"release.json", "SHA256SUMS"}:
        raise ValueError("release directory contents differ from the manifest")
    for name, digest in manifest["sha256"].items():
        if sha256(directory / name) != digest:
            raise ValueError(f"checksum mismatch: {name}")
    hashes = {**manifest["sha256"], "release.json": sha256(directory / "release.json")}
    checksums = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    if (directory / "SHA256SUMS").read_text() != checksums:
        raise ValueError("SHA256SUMS differs from the release manifest")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    native = commands.add_parser("pack")
    native.add_argument("--binary", type=Path, required=True)
    native.add_argument("--platform", choices=TARGETS, required=True)
    native.add_argument("--output", type=Path, default=ROOT / "dist/native")
    assemble = commands.add_parser("bundle")
    assemble.add_argument("--input", type=Path, required=True)
    assemble.add_argument("--output", type=Path, default=ROOT / "dist/release")
    validate = commands.add_parser("verify")
    validate.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "pack":
        pack(args.binary, args.platform, args.output)
    elif args.command == "bundle":
        bundle(args.input, args.output)
    else:
        verify(args.directory)
