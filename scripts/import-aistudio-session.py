#!/usr/bin/env python3
"""Import a local Google-only browser export into this AI Studio deployment."""
import argparse
import hashlib
import http.cookiejar
import json
import math
import os
import pathlib
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import proxykit


def normalize(data, now=None):
    now = time.time() if now is None else now
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise ValueError("Expected a browser storage-state JSON with a cookies array")
    cookies = []
    for cookie in data["cookies"]:
        if not isinstance(cookie, dict) or not isinstance(cookie.get("domain"), str):
            raise ValueError("Invalid cookie domain")
        domain = cookie.get("domain", "").lstrip(".").lower()
        if domain != "google.com" and not domain.endswith(".google.com"):
            raise ValueError("Export contains cookies outside google.com; use the Google-only exporter")
        expires = cookie.get("expires", -1)
        if not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise ValueError("Invalid cookie expiration")
        if expires != -1 and expires <= now:
            continue
        item = {key: cookie[key] for key in ("name", "value", "domain", "path", "httpOnly", "secure", "sameSite")}
        if not all(isinstance(item[k], str) for k in ("name", "value", "domain", "path")):
            raise ValueError("Invalid cookie fields")
        if item["sameSite"] not in ("Strict", "Lax", "None"):
            raise ValueError("Invalid cookie sameSite")
        if not isinstance(item["httpOnly"], bool) or not isinstance(item["secure"], bool):
            raise ValueError("Invalid cookie flags")
        item["expires"] = expires
        cookies.append(item)
    if not any(re.fullmatch(r"SID|__Secure-[13]PSID", c["name"]) and c["value"] for c in cookies):
        raise ValueError("Export has no unexpired Google sign-in cookie")
    name = data.get("accountName") or "Local Google"
    if not isinstance(name, str) or len(name) > 160 or any(c in name for c in "\r\n\0"):
        raise ValueError("Invalid account label")
    return {"accountName": name, "cookies": cookies, "origins": []}


def save_auth(state, directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    indices = []
    for file in directory.glob("auth-*.json"):
        match = re.fullmatch(r"auth-(\d+)\.json", file.name)
        if not match:
            continue
        indices.append(int(match[1]))
        if json.loads(file.read_text()).get("proxyKitSourceDigest") == digest:
            return file, False
    state = dict(state, proxyKitSourceDigest=digest)
    index = max(indices, default=-1) + 1
    # Publish a complete file without overwriting a concurrent import. The server
    # notices filename changes, so it must never see an empty/partial auth file.
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".import-", delete=False) as out:
        pending = pathlib.Path(out.name)
        json.dump(state, out)
    try:
        while True:
            file = directory / f"auth-{index}.json"
            try:
                os.link(pending, file)
                return file, True
            except FileExistsError:
                index += 1
    finally:
        pending.unlink()


def reload_auth(values):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = proxykit.service_url("aistudio", values)
    body = urllib.parse.urlencode({"password": values["AISTUDIO_WEB_PASSWORD"]}).encode()
    with opener.open(base + "/login", body, timeout=20) as response:
        response.read()
        if response.geturl().rstrip("/").endswith("/login"):
            raise ValueError("AI Studio console login failed")
    with opener.open(base + "/api/status", timeout=30) as response:
        json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path, help="Locally exported browser session JSON")
    parser.add_argument("--env-file", type=pathlib.Path, default=proxykit.ROOT / "runtime/stack.env")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait for the browser export to appear")
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait
    while not args.source.exists() and time.monotonic() < deadline:
        time.sleep(1)
    if args.source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Session export is unexpectedly large")
    state = normalize(json.loads(args.source.read_text(encoding="utf-8-sig")))
    file, created = save_auth(state, proxykit.ROOT / "runtime/aistudio/auth")
    print(f"{'Imported' if created else 'Reused'} {len(state['cookies'])} Google cookies in {file.name} (mode 600).", flush=True)
    reload_auth(proxykit.read_env(args.env_file))
    print("AI Studio reloaded the local session; verify browser connection and real generation next.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, urllib.error.HTTPError):
            print(f"ERROR: AI Studio console returned HTTP {error.code}", file=sys.stderr)
        elif isinstance(error, KeyError):
            print("ERROR: Session or local configuration is missing a required field", file=sys.stderr)
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
