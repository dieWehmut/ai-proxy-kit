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


READY_POLL_INTERVAL = 1.0


def normalize(data, now=None):
    now = time.time() if now is None else now
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise ValueError("Expected a browser storage-state JSON with a cookies array")
    cookies = []
    fields = ("name", "value", "domain", "path", "httpOnly", "secure", "sameSite")
    for index, cookie in enumerate(data["cookies"], start=1):
        if not isinstance(cookie, dict):
            raise ValueError(f"Cookie #{index} must be an object")
        for field in fields:
            if field not in cookie:
                raise ValueError(f"Cookie #{index} is missing required field '{field}'")
        if not isinstance(cookie["domain"], str):
            raise ValueError(f"Cookie #{index} has an invalid domain")
        domain = cookie.get("domain", "").lstrip(".").lower()
        if domain != "google.com" and not domain.endswith(".google.com"):
            raise ValueError("Export contains cookies outside google.com; use the Google-only exporter")
        expires = cookie.get("expires", -1)
        if type(expires) not in (int, float) or not math.isfinite(expires):
            raise ValueError(f"Cookie #{index} has an invalid expiration")
        if expires != -1 and expires <= now:
            continue
        item = {key: cookie[key] for key in fields}
        if not all(isinstance(item[k], str) for k in ("name", "value", "domain", "path")):
            raise ValueError(f"Cookie #{index} requires string name, value, domain and path fields")
        if item["sameSite"] not in ("Strict", "Lax", "None"):
            raise ValueError(f"Cookie #{index} has an invalid sameSite")
        if not isinstance(item["httpOnly"], bool) or not isinstance(item["secure"], bool):
            raise ValueError(f"Cookie #{index} requires boolean httpOnly and secure fields")
        item["expires"] = expires
        cookies.append(item)
    if not any(re.fullmatch(r"SID|__Secure-[13]PSID", c["name"]) and c["value"] for c in cookies):
        raise ValueError("Export has no unexpired Google sign-in cookie (SID, __Secure-1PSID or __Secure-3PSID). Export a fresh signed-in session")
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


def console_url(values, base_url=None):
    if not values.get("AISTUDIO_WEB_PASSWORD"):
        raise ValueError("AI Studio configuration is missing required field AISTUDIO_WEB_PASSWORD")
    if base_url is None:
        if not values.get("AISTUDIO_PORT"):
            raise ValueError("AI Studio configuration is missing AISTUDIO_PORT. Supply --base-url or configure the port")
        base_url = proxykit.service_url("aistudio", values)
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise ValueError("AI Studio base URL must be a valid HTTP or HTTPS console URL") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or
            any(char.isspace() for char in base_url) or port == 0):
        raise ValueError("AI Studio base URL must be HTTP or HTTPS with a host and optional path, without credentials, query or fragment")
    return base_url.rstrip("/")


def status_summary(data, auth_index=None):
    if not isinstance(data, dict):
        raise ValueError("AI Studio status response must be an object")
    status = data.get("status", data)
    if not isinstance(status, dict) or not isinstance(status.get("browserConnected"), bool):
        raise ValueError("AI Studio status response requires boolean browserConnected")
    current = status.get("currentAuthIndex", -1)
    busy = status.get("isSystemBusy", False)
    accounts = status.get("accountDetails", [])
    if type(current) is not int or current < -1 or not isinstance(busy, bool):
        raise ValueError("AI Studio status response has invalid currentAuthIndex or isSystemBusy")
    if not isinstance(accounts, list):
        raise ValueError("AI Studio status response requires an accountDetails array")
    for account in accounts:
        if (not isinstance(account, dict) or type(account.get("index")) is not int or
                account["index"] < 0 or
                any(not isinstance(account.get(field, False), bool) for field in ("isExpired", "isInvalid"))):
            raise ValueError("AI Studio status response has invalid accountDetails fields")
    indices = {account["index"] for account in accounts}
    expired = {account["index"] for account in accounts if account.get("isExpired")}
    invalid = {account["index"] for account in accounts if account.get("isInvalid")}
    imported = "unchecked"
    if auth_index is not None:
        imported = "recognized"
        if auth_index not in indices:
            imported = "missing"
        elif auth_index in invalid:
            imported = "invalid"
        elif auth_index in expired:
            imported = "expired"
    if auth_index is None:
        ready = (status["browserConnected"] and not busy and current in indices and
                 current not in expired | invalid)
    else:
        # A healthy connection to a different account is not proof that this
        # import became active. Readiness requires the exact imported index.
        ready = (status["browserConnected"] and not busy and current == auth_index and
                 imported == "recognized")
    return dict(browserConnected=status["browserConnected"], currentAuthIndex=current,
                isSystemBusy=busy, expiredAccounts=len(expired), invalidAccounts=len(invalid),
                importedAccount=imported, ready=ready)


class ReadinessTimeout(ValueError):
    def __init__(self, timeout, summary):
        details = ", ".join(f"{key}={str(value).lower()}" for key, value in summary.items())
        super().__init__(f"AI Studio did not become ready within {timeout} seconds ({details})")


def read_status(opener, base):
    with opener.open(base + "/api/status", timeout=30) as response:
        if urllib.parse.urlsplit(response.geturl()).path.rstrip("/").endswith("/login"):
            raise ValueError("AI Studio console session expired before the status check")
        try:
            return json.load(response)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("AI Studio status response must be valid JSON") from None


def wait_for_ready(opener, base, auth_index, ready_timeout):
    if ready_timeout < 0:
        raise ValueError("ready timeout must be zero or greater")
    deadline = time.monotonic() + ready_timeout
    while True:
        summary = status_summary(read_status(opener, base), auth_index)
        if summary["ready"] or summary["importedAccount"] in ("expired", "invalid"):
            return summary
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeout(ready_timeout, summary)
        time.sleep(min(READY_POLL_INTERVAL, remaining))


def reload_auth(values, base_url=None, auth_index=None, ready_timeout=0):
    base = console_url(values, base_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    body = urllib.parse.urlencode({"password": values["AISTUDIO_WEB_PASSWORD"]}).encode()
    with opener.open(base + "/login", body, timeout=20) as response:
        response.read()
        if urllib.parse.urlsplit(response.geturl()).path.rstrip("/").endswith("/login"):
            raise ValueError("AI Studio console login failed")
    return wait_for_ready(opener, base, auth_index, ready_timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path, help="Locally exported browser session JSON")
    parser.add_argument("--env-file", type=pathlib.Path, default=proxykit.ROOT / "runtime/stack.env")
    parser.add_argument("--auth-dir", type=pathlib.Path, default=proxykit.ROOT / "runtime/aistudio/auth",
                        help="Deployment auth directory mounted at /app/configs/auth")
    parser.add_argument("--base-url", help="AI Studio console base URL, default http://127.0.0.1:<AISTUDIO_PORT>")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait for the browser export to appear")
    parser.add_argument("--ready-timeout", type=int, default=60,
                        help="Seconds to wait for the imported account to become active (default: 60)")
    args = parser.parse_args()
    if args.wait < 0:
        parser.error("--wait must be zero or greater")
    if args.ready_timeout < 0:
        parser.error("--ready-timeout must be zero or greater")
    deadline = time.monotonic() + args.wait
    while not args.source.exists() and time.monotonic() < deadline:
        time.sleep(1)
    if not args.source.is_file():
        raise ValueError(f"Session export file is missing or is not a regular file at {args.source} after waiting {args.wait} seconds")
    if args.source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Session export is unexpectedly large")
    try:
        data = json.loads(args.source.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Session export must be valid UTF-8 browser storage-state JSON") from None
    state = normalize(data)
    if not args.env_file.is_file():
        raise ValueError(f"AI Studio env file is missing or is not a regular file at {args.env_file}")
    values = proxykit.read_env(args.env_file)
    base = console_url(values, args.base_url)
    file, created = save_auth(state, args.auth_dir)
    print(f"{'Imported' if created else 'Reused'} {len(state['cookies'])} Google cookies in {file.name} (mode 600).", flush=True)
    auth_index = int(file.stem.split("-")[1])
    try:
        summary = reload_auth(values, base, auth_index, ready_timeout=args.ready_timeout)
    except ReadinessTimeout as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    details = ", ".join(f"{key}={str(value).lower()}" for key, value in summary.items())
    print(f"AI Studio {details}.", flush=True)
    if summary["ready"]:
        print("Verify real generation through Sub2API next.", flush=True)
        return 0
    if summary["importedAccount"] in ("expired", "invalid"):
        print(f"Imported AI Studio {file.name} is {summary['importedAccount']}; export a fresh Google session.", flush=True)
        return 1
    print("Check the deployment auth mount and complete Google login in AI Studio's Web/VNC console, then recheck readiness.", flush=True)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, urllib.error.HTTPError):
            print(f"ERROR: AI Studio console returned HTTP {error.code}", file=sys.stderr)
        elif isinstance(error, KeyError):
            print("ERROR: Session or local configuration is missing a required field", file=sys.stderr)
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
