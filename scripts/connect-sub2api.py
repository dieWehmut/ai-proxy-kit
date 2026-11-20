#!/usr/bin/env python3
"""Add this stack's gateways to Sub2API using its authenticated admin API."""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import proxykit


def items(data):
    return data if isinstance(data, list) else data.get("items", data.get("accounts", []))


class Sub2API:
    def __init__(self, base, values, cli=None):
        self.base = base.rstrip("/")
        self.values = values
        self.cli = cli
        self.jwt = os.environ.get("SUB2API_JWT") or values.get("SUB2API_JWT", "")
        self.admin_key = os.environ.get("SUB2API_ADMIN_API_KEY") or values.get("SUB2API_ADMIN_API_KEY", "")
        if not self.jwt and not self.admin_key:
            login = self.http("POST", "/api/v1/auth/login", {
                "email": values["SUB2API_EMAIL"], "password": values["SUB2API_PASSWORD"]}, auth=False)
            self.jwt = login["access_token"]

    def http(self, method, path, body=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            if path.startswith("/api/v1/admin/") and self.admin_key:
                headers["x-api-key"] = self.admin_key
            elif self.jwt:
                headers["Authorization"] = "Bearer " + self.jwt
            else:
                raise ValueError("User API operations need SUB2API_JWT or administrator email/password login")
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with proxykit.HTTP.open(req, timeout=120) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                data = json.load(error)
                reason = data.get("reason", data.get("message", "Request rejected"))
            except ValueError:
                reason = "Request rejected"
            raise ValueError(f"Sub2API {method} {path}: HTTP {error.code} ({reason})") from None
        if data.get("code", 0) != 0:
            raise ValueError("Sub2API API rejected the operation")
        return data.get("data", data)

    def admin(self, method, path, body=None):
        if not self.cli:
            return self.http(method, "/api/v1/admin/" + path, body)
        env = dict(os.environ, SUB2API_BASE_URL=self.base, SUB2API_JWT=self.jwt,
                   SUB2API_ADMIN_API_KEY=self.admin_key)
        with tempfile.TemporaryDirectory(prefix="sub2api-admin-") as folder:
            command = ["node", str(self.cli), "api", method, "/admin/" + path]
            if body is not None:
                payload = pathlib.Path(folder) / "payload.json"
                proxykit.private_write(payload, json.dumps(body))
                command += ["--file", str(payload)]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Do not echo payloads or CLI output that might contain upstream keys.
            raise ValueError(f"Bundled Sub2API admin CLI failed: {method} {path}")
        return json.loads(result.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=pathlib.Path, default=proxykit.ROOT / "runtime/stack.env")
    p.add_argument("--base-url", help="Sub2API admin URL, default: local stack port")
    p.add_argument("--admin-cli", type=pathlib.Path, help="Use an existing sub2api-admin skill CLI for admin operations")
    p.add_argument("--provider", choices=("all", "antigravity", "workbuddy", "aistudio"), default="all")
    p.add_argument("--upstream-url", help="Override container-network upstream URL (single provider only)")
    p.add_argument("--test", action="store_true", help="Create/reuse a provider-scoped API key and verify routed generation")
    p.add_argument("--stream", action="store_true", help="--test: verify a real SSE response including [DONE]")
    p.add_argument("--model", help="Model for --test (single provider); otherwise discover WorkBuddy model")
    p.add_argument("--acknowledge-compliance", action="store_true", help="Explicitly submit Sub2API's published first-login acknowledgement; read its document first")
    args = p.parse_args()
    values = proxykit.read_env(args.env_file)
    local = proxykit.ROOT / ".env.sub2api.local"
    values.update(proxykit.read_env(local))
    for key in ("SUB2API_EMAIL", "SUB2API_PASSWORD"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    base = args.base_url or os.environ.get("SUB2API_BASE_URL") or values.get("SUB2API_BASE_URL") or proxykit.service_url("sub2api", values)
    api = Sub2API(base, values, args.admin_cli)
    compliance = api.admin("GET", "compliance")
    if compliance.get("required"):
        if not args.acknowledge_compliance:
            raise ValueError("Sub2API requires first-login acknowledgement. Read " + compliance["document_url_zh"] +
                             " and confirm in its UI, or rerun with --acknowledge-compliance after agreeing.")
        api.admin("POST", "compliance/accept", {"phrase": compliance["ack_phrase_zh"], "language": "zh"})
    providers = ["antigravity", "workbuddy"] if args.provider == "all" else [args.provider]
    if args.upstream_url and len(providers) != 1:
        raise ValueError("--upstream-url requires a single provider")
    groups = items(api.admin("GET", "groups/all"))
    # Page through existing accounts to avoid creating duplicates on reruns.
    accounts, page = [], 1
    while True:
        data = api.admin("GET", f"accounts?page={page}&page_size=100")
        batch = items(data)
        accounts += batch
        if len(batch) < 100:
            break
        page += 1
    receipts_path = proxykit.ROOT / "runtime/sub2api-connections.json"
    receipts = json.loads(receipts_path.read_text()) if receipts_path.exists() else {}
    for name in providers:
        label = "AI Proxy Kit / " + name.capitalize()
        group = next((g for g in groups if g["name"] == label), None)
        if group and group.get("platform") != "openai":
            raise ValueError(f"Group {label} exists with a different platform; inspect it before proceeding")
        if not group:
            group = api.admin("POST", "groups", {"name": label, "platform": "openai", "rate_multiplier": 1,
                                                "subscription_type": "standard", "is_exclusive": False})
            groups.append(group)
        ports = {"antigravity": 8045, "workbuddy": 7863, "aistudio": 7860}
        url = args.upstream_url or f"http://{name}:{ports[name]}"
        credential = {"base_url": url, "api_key": values[name.upper() + "_API_KEY"]}
        account = next((a for a in accounts if a["name"] == label), None)
        if account:
            detail = api.admin("GET", f"accounts/{account['id']}")
            if detail.get("platform") != "openai" or detail.get("type") != "apikey":
                raise ValueError(f"Account {label} has a different platform/type; no changes applied")
            stored = detail.get("credentials", {})
            # Sub2API redacts API keys from account reads; compare them only when exposed.
            if (stored.get("base_url") != url or
                    ("api_key" in stored and stored["api_key"] != credential["api_key"])):
                raise ValueError(f"Account {label} has different credentials; review it before updating")
        else:
            account = api.admin("POST", "accounts", {
                "name": label, "platform": "openai", "type": "apikey", "credentials": credential,
                "extra": {"openai_responses_mode": "force_chat_completions", "openai_responses_supported": False},
                "concurrency": 2, "priority": 1, "rate_multiplier": 1, "group_ids": [group["id"]],
                "notes": "Managed by ai-proxy-kit; authenticated local gateway over the Compose network.",
            })
            accounts.append(account)
        verified = api.admin("GET", f"accounts/{account['id']}")
        if group["id"] not in verified.get("group_ids", []):
            raise ValueError(f"Account {label} is not attached to its expected group")
        print(f"Sub2API: {label}: account={account['id']} group={group['id']} upstream={url}", flush=True)
        receipt = {"base_url": base, "account_id": account["id"], "group_id": group["id"], "upstream_url": url}
        receipts[name] = receipt
        proxykit.private_write(receipts_path, json.dumps(receipts, indent=2))
        if args.test:
            keys, key_page = [], 1
            while True:
                batch = items(api.http("GET", f"/api/v1/keys?page={key_page}&page_size=100"))
                keys += batch
                if len(batch) < 100:
                    break
                key_page += 1
            key_name = "ai-proxy-kit-" + name
            key = next((k for k in keys if k["name"] == key_name and k.get("group_id") == group["id"]), None)
            if not key:
                key = api.http("POST", "/api/v1/keys", {"name": key_name, "group_id": group["id"]})
            if not key.get("key"):
                key = api.http("GET", "/api/v1/keys/" + str(key["id"]))
            # Save the accepted identity before generation so retrying never creates another key.
            receipt.update(api_key_id=key["id"], api_key=key["key"])
            proxykit.private_write(receipts_path, json.dumps(receipts, indent=2))
            model = args.model or ("gemini-3.8-flash-low" if name == "antigravity" else None)
            if not model:
                models = proxykit.request(proxykit.service_url(name, values) + "/v1/models",
                                          values[name.upper() + "_API_KEY"]).get("data", [])
                ids = [m["id"] for m in models]
                preferred = ("cn:hy4flash-free", "cn:deepseek-v4-flash", "deepseek-v4-flash", "deepseek-v3.2", "glm-5.2")
                model = next((m for m in preferred if m in ids), ids[0] if ids else None)
            if not model:
                raise ValueError(f"{name} has no available models; authorize an account first")
            proxykit.verify_generation(base, key["key"], model, args.stream)
            receipt.update(tested_model=model, generation="PASS", stream=args.stream)
            proxykit.private_write(receipts_path, json.dumps(receipts, indent=2))
            print(f"sub2api -> {name} -> {model}: real generation PASS (PROXY_KIT_OK)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
