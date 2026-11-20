#!/usr/bin/env python3
"""Build WorkBuddy v1.13.0 with Go's standard proxy environment support."""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import proxykit

COMMIT = "d66384d9e3d69655f799d7d8f2e5fba1946e00ab"
SOURCE_SHA256 = "25aeb79df9d0b6359207e14debcb13fd919e7e9fe73965286a302c91f4038537"


def go_binary():
    existing = shutil.which("go") or ("/usr/local/go/bin/go" if pathlib.Path("/usr/local/go/bin/go").is_file() else None)
    if existing:
        version = subprocess.check_output([existing, "version"], text=True).split()[2].removeprefix("go")
        numbers = tuple(int(n) for n in version.split(".")[:3])
        if numbers >= (1, 22, 5):
            return existing
    cached = proxykit.ROOT / "runtime/toolchain/go/bin/go"
    if cached.is_file():
        return str(cached)
    # Install an isolated, checksum-verified toolchain; do not change the system Go.
    with urllib.request.urlopen("https://go.dev/dl/?mode=json&include=all", timeout=30) as response:
        releases = json.load(response)
    arch = "arm64" if __import__("platform").machine() in ("aarch64", "arm64") else "amd64"
    release = next(r for r in releases if r["version"] == "go1.23.6")
    asset = next(f for f in release["files"] if f["os"] == "linux" and f["arch"] == arch and f["kind"] == "archive")
    toolchain = cached.parents[2]
    toolchain.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=toolchain) as folder:
        archive = pathlib.Path(folder) / asset["filename"]
        proxykit.download("https://go.dev/dl/" + asset["filename"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("Go toolchain checksum mismatch")
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                path = pathlib.PurePosixPath(member.name)
                if member.issym() or member.islnk() or path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unexpected path in Go archive")
                if member.isfile():
                    target = toolchain.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as inp, target.open("wb") as out:
                        shutil.copyfileobj(inp, out)
                    target.chmod(member.mode & 0o777)
    return str(cached)


def build(env_file):
    if not proxykit.read_env(env_file):
        raise ValueError("Run install.sh --prepare-only before building the optional patch")
    binary = proxykit.ROOT / "runtime/workbuddy/wb2api-proxy"
    receipt_path = binary.with_suffix(".json")
    if binary.is_file() and receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("commit") == COMMIT and hashlib.sha256(binary.read_bytes()).hexdigest() == receipt.get("binary_sha256"):
            enable_patch(env_file)
            print("Reusing verified WorkBuddy environment-proxy binary.")
            return
    archive_cache = pathlib.Path.home() / ".cache/ai-proxy-kit/workbuddy-v1.13.0.tar.gz"
    archive_cache.parent.mkdir(parents=True, exist_ok=True)
    if not archive_cache.is_file() or hashlib.sha256(archive_cache.read_bytes()).hexdigest() != SOURCE_SHA256:
        proxykit.download("https://codeload.github.com/linguo2625469/workbuddy2api-panel/tar.gz/" + COMMIT, archive_cache)
    if hashlib.sha256(archive_cache.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("WorkBuddy source archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="workbuddy-build-", dir=proxykit.ROOT / "runtime") as folder:
        source = pathlib.Path(folder)
        with tarfile.open(archive_cache) as tar:
            for member in tar.getmembers():
                relative = pathlib.PurePosixPath(member.name)
                if member.issym() or member.islnk() or relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unexpected path in WorkBuddy source archive")
                if member.isfile():
                    target = source.joinpath(*relative.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as inp, target.open("wb") as out:
                        shutil.copyfileobj(inp, out)
        transport = source / "internal/upstream/transport.go"
        text = transport.read_text()
        needle = "return &http.Transport{\n"
        if text.count(needle) != 1:
            raise ValueError("Upstream Transport changed; review the proxy patch before building")
        transport.write_text(text.replace(needle, needle + "\t\tProxy: http.ProxyFromEnvironment,\n", 1))
        output = source / "wb2api-proxy"
        subprocess.run([go_binary(), "build", "-trimpath", "-ldflags=-s -w", "-o", str(output), "./cmd/server"],
                       cwd=source, env=dict(os.environ, CGO_ENABLED="0"), check=True, timeout=600)
        binary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(output, binary)
        binary.chmod(0o700)
    # Keep the upstream license beside this derivative binary; it stays in ignored runtime/.
    with tarfile.open(archive_cache) as tar:
        member = next(m for m in tar.getmembers() if pathlib.PurePosixPath(m.name).name == "LICENSE")
        with tar.extractfile(member) as inp:
            proxykit.private_write(binary.parent / "WORKBUDDY-LICENSE", inp.read().decode())
    proxykit.private_write(receipt_path, json.dumps({"commit": COMMIT, "source_sha256": SOURCE_SHA256,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "patch": "http.Transport.Proxy = http.ProxyFromEnvironment"}, indent=2))
    enable_patch(env_file)
    print("Built WorkBuddy with HTTP_PROXY/HTTPS_PROXY/NO_PROXY support; restart workbuddy to activate.")


def enable_patch(env_file):
    # Compilation/downloads can take minutes. Preserve changes made while they ran.
    current = proxykit.read_env(env_file)
    current["WORKBUDDY_ENV_PROXY_PATCH"] = "true"
    proxykit.save_env(env_file, current)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=pathlib.Path, default=proxykit.ROOT / "runtime/stack.env")
    args = parser.parse_args()
    try:
        build(args.env_file)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
