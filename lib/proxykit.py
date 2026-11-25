#!/usr/bin/env python3
"""Idempotent Linux/WSL lifecycle management for the local API gateways."""

import argparse
import hashlib
import http.cookiejar
import json
import os
import pathlib
import platform
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ("antigravity", "workbuddy", "sub2api", "aistudio")
DEFAULTS = {
    "ANTIGRAVITY_PORT": "8045", "WORKBUDDY_PORT": "7863", "SUB2API_PORT": "8080", "AISTUDIO_PORT": "2048",
    "ANTIGRAVITY_IMAGE": "lbjlaq/antigravity-manager:v5.0.3",
    "WORKBUDDY_IMAGE": "ghcr.io/linguo2625469/workbuddy2api-panel:1.13.0",
    "SUB2API_IMAGE": "weishaw/sub2api:0.2.23",
    "AISTUDIO_IMAGE": "ghcr.io/mag1cfall/aistudio2api:latest",
    "SUB2API_EMAIL": "admin@sub2api.local", "UPSTREAM_PROXY": "", "TZ": "Asia/Shanghai",
}
SECRET_KEYS = ("ANTIGRAVITY_API_KEY", "ANTIGRAVITY_WEB_PASSWORD", "WORKBUDDY_API_KEY", "AISTUDIO_API_KEY", "AISTUDIO_ADMIN_PASSWORD",
               "SUB2API_PASSWORD", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "JWT_SECRET", "TOTP_ENCRYPTION_KEY")
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_env(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env entry in {path}")
        key, value = line.split("=", 1)
        if key in values:
            raise ValueError(f"Duplicate env variable: {key}")
        if value.startswith(("'", '"')):
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError(f"Invalid quoted value for {key}")
            value = parts[0]
        values[key] = value
    return values


def private_write(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
        temp.write(text)
        temporary = pathlib.Path(temp.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def save_env(path, values):
    lines = []
    for key, value in values.items():
        if "\n" in value or "\r" in value or "'" in value:
            raise ValueError(f"Unsupported newline or single quote in env variable: {key}")
        # Compose single quotes keep dollars literal; the file is never sourced.
        lines.append(f"{key}='{value}'")
    private_write(path, "\n".join(lines) + "\n")


def initialize(args):
    values = read_env(args.env_file)
    if args.import_antigravity:
        old = read_env(args.import_antigravity)
        for old_name, name in (("API_KEY", "ANTIGRAVITY_API_KEY"), ("WEB_PASSWORD", "ANTIGRAVITY_WEB_PASSWORD")):
            if old.get(old_name):
                values.setdefault(name, old[old_name])
    for key, value in DEFAULTS.items():
        values.setdefault(key, value)
    # Migration from the retired Build-channel deployment: same secrets under
    # new names, and the console port moved from 7860 to the Playground 2048.
    if values.get("AISTUDIO_WEB_PASSWORD") and not values.get("AISTUDIO_ADMIN_PASSWORD"):
        values["AISTUDIO_ADMIN_PASSWORD"] = values["AISTUDIO_WEB_PASSWORD"]
    values.pop("AISTUDIO_WEB_PASSWORD", None)
    if values.get("AISTUDIO_PORT") == "7860":
        values["AISTUDIO_PORT"] = DEFAULTS["AISTUDIO_PORT"]
    for key in SECRET_KEYS:
        if not values.get(key):
            values[key] = secrets.token_hex(32)
    for name in SERVICES:
        port = getattr(args, name + "_port", None)
        if port is not None:
            values[name.upper() + "_PORT"] = str(port)
    if args.upstream_proxy is not None:
        values["UPSTREAM_PROXY"] = args.upstream_proxy
    if args.antigravity_data:
        values["ANTIGRAVITY_DATA_DIR"] = str(args.antigravity_data.expanduser().resolve())
    values.setdefault("ANTIGRAVITY_DATA_DIR", str(ROOT / "runtime/antigravity"))
    values.setdefault("LOCAL_UID", str(os.getuid()))
    values.setdefault("LOCAL_GID", str(os.getgid()))
    ports = [int(values[name.upper() + "_PORT"]) for name in SERVICES]
    if len(set(ports)) != len(ports) or any(not 1 <= p <= 65535 for p in ports):
        raise ValueError("Gateway ports must be distinct numbers between 1 and 65535")
    save_env(args.env_file, values)
    for folder in ("antigravity", "workbuddy/auths", "workbuddy/data", "aistudio/data"):
        (ROOT / "runtime" / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    pathlib.Path(values["ANTIGRAVITY_DATA_DIR"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    workbuddy = ROOT / "runtime/workbuddy/config.json"
    if not workbuddy.exists():
        config = {
            "listen": "0.0.0.0:7863", "api_key": values["WORKBUDDY_API_KEY"],
            "auth_dir": "/config/auths", "state_file": "/config/data/state.json",
            "schedule": {name + "_enabled": False for name in
                         ("checkin", "growth", "travel", "activity", "keepalive", "blackcat")},
        }
        private_write(workbuddy, json.dumps(config, indent=2) + "\n")
    if args.import_workbuddy:
        source = args.import_workbuddy.expanduser().resolve() / "auths"
        if not source.is_dir():
            raise ValueError("WorkBuddy import directory must contain auths/")
        copied = 0
        for file in source.glob("*.json"):
            target = ROOT / "runtime/workbuddy/auths" / file.name
            if not target.exists():
                json.loads(file.read_text())
                private_write(target, file.read_text())
                copied += 1
        print(f"Imported {copied} WorkBuddy account files; source files unchanged.", flush=True)
    print(f"Configuration ready: {args.env_file} (mode 600); existing keys retained.", flush=True)
    return values


def write_local_access(values):
    """Regenerate local access hints from stack.env so they never drift."""
    port = {name: values.get(name.upper() + "_PORT", DEFAULTS[name.upper() + "_PORT"]) for name in SERVICES}
    lines = [
        "AI Proxy Kit - 本机访问（本文件含密钥，请只在本机查看）",
        "",
        f"Sub2API: http://127.0.0.1:{port['sub2api']}",
        f"管理员邮箱: {values.get('SUB2API_EMAIL', 'admin@sub2api.local')}",
        f"管理员密码: {values['SUB2API_PASSWORD']}",
        "",
        f"WorkBuddy: http://127.0.0.1:{port['workbuddy']}/panel/",
        f"API / 管理 Key: {values['WORKBUDDY_API_KEY']}",
        "",
        f"Antigravity: http://127.0.0.1:{port['antigravity']}",
        f"管理密码: {values['ANTIGRAVITY_WEB_PASSWORD']}",
        f"API Key: {values['ANTIGRAVITY_API_KEY']}",
        "",
        f"AI Studio (Playground): http://127.0.0.1:{port['aistudio']}",
        f"管理密码: {values['AISTUDIO_ADMIN_PASSWORD']}（用户名 {values.get('AISTUDIO_ADMIN_USER', 'admin')}）",
        f"API Key: {values['AISTUDIO_API_KEY']}",
        "",
        "一键启动: 在项目根目录执行 bash start.sh all；也可单独执行 bash start.sh aistudio。",
        "Windows 入口: install.cmd、start.cmd、status.cmd、check.cmd、open-aistudio.cmd；全部网关监听 0.0.0.0。",
    ]
    private_write(ROOT / "runtime/LOCAL_ACCESS.txt", "\n".join(lines) + "\n")
    console = (
        '<!doctype html><html lang="zh"><meta charset="utf-8">'
        '<title>本机 AI Studio 控制台</title>'
        '<p>正在打开本机 AI Studio（Playground）控制台…</p>'
        f'<script>location.replace("http://127.0.0.1:{port["aistudio"]}/")</script>'
        f'<p><a href="http://127.0.0.1:{port["aistudio"]}/">手动打开</a></p></html>'
    )
    private_write(ROOT / "runtime/open-aistudio.html", console)


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def docker_command():
    if not shutil.which("docker"):
        raise ValueError("Docker is missing. Run bash install.sh --install-docker on Linux, or enable WSL Docker integration.")
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return ["docker"]
    if shutil.which("sudo") and subprocess.run(["sudo", "-n", "docker", "info"], stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL).returncode == 0:
        return ["sudo", "-n", "docker"]
    raise ValueError("Docker Engine is not reachable by this user. Start it and grant Docker access, then retry.")


def compose(docker, args, values, *command, capture=False):
    env = dict(os.environ, **values)
    files = ["-f", str(ROOT / "compose.yml")]
    if values.get("WORKBUDDY_ENV_PROXY_PATCH") == "true":
        files += ["-f", str(ROOT / "compose.workbuddy-proxy.yml")]
    return run([*docker, "compose", "--project-directory", str(ROOT), "--env-file", str(args.env_file),
                *files, *command], env=env, text=True,
               stdout=subprocess.PIPE if capture else None)


def download(url, destination):
    req = urllib.request.Request(url, headers={"User-Agent": "ai-proxy-kit"})
    with urllib.request.urlopen(req, timeout=90) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def crane_binary():
    existing = shutil.which("crane")
    if existing:
        return existing
    cache = pathlib.Path.home() / ".cache/ai-proxy-kit"
    binary = cache / "bin/crane"
    if binary.is_file():
        return str(binary)
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(platform.machine())
    if not arch:
        raise ValueError("No crane fallback available for this architecture")
    asset = f"go-containerregistry_Linux_{arch}.tar.gz"
    base = "https://github.com/google/go-containerregistry/releases/download/v0.20.6/"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as folder:
        folder = pathlib.Path(folder)
        archive, checksums = folder / asset, folder / "checksums.txt"
        download(base + asset, archive)
        download(base + "checksums.txt", checksums)
        expected = [line.split()[0] for line in checksums.read_text().splitlines()
                    if line.split()[-1].lstrip("*") == asset]
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if expected != [digest]:
            raise ValueError("crane archive SHA-256 verification failed")
        with tarfile.open(archive) as tar:
            member = next(m for m in tar.getmembers() if m.name in ("crane", "./crane"))
            binary.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, binary.open("wb") as output:
                shutil.copyfileobj(source, output)
        binary.chmod(0o700)
    return str(binary)


def ensure_images(docker, args, values, services):
    model = json.loads(compose(docker, args, values, "--profile", "*", "config", "--format", "json", capture=True).stdout)
    names = list(services) + (["postgres", "redis"] if "sub2api" in services else [])
    for name in names:
        image = model["services"][name]["image"]
        if subprocess.run([*docker, "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            print(f"Using cached image: {image}", flush=True)
            continue
        if args.skip_pull:
            raise ValueError(f"Image {image} is missing (--skip-pull was requested)")
        print(f"Pulling {image}...", flush=True)
        try:
            result = subprocess.run([*docker, "pull", image], timeout=100)
            if result.returncode == 0:
                continue
        except subprocess.TimeoutExpired:
            pass
        print("Docker registry access failed; retrying through the shell proxy with crane.", flush=True)
        arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"
        with tempfile.TemporaryDirectory(prefix="ai-proxy-image-") as folder:
            tar = str(pathlib.Path(folder) / "image.tar")
            run([crane_binary(), "pull", "--platform", "linux/" + arch, image, tar], timeout=600)
            run([*docker, "load", "--input", tar], timeout=600)


def request(url, key=None, body=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with HTTP.open(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def service_url(name, values):
    return "http://127.0.0.1:" + values[name.upper() + "_PORT"]


def aistudio_account_status(data):
    """Return secret-free readiness details from an authenticated /api/status response."""
    if not isinstance(data, dict):
        raise ValueError("AI Studio status response must be an object")
    state = data.get("state")
    running = data.get("running")
    accounts = data.get("accounts")
    if not isinstance(state, str) or not isinstance(running, bool):
        raise ValueError("AI Studio status response requires string state and boolean running")
    if not running:
        raise ValueError(f"AI Studio generation service is {state}; start it from the console")
    if not isinstance(accounts, dict):
        raise ValueError("AI Studio status response requires an accounts object")
    ready = accounts.get("ready")
    total = accounts.get("total")
    if type(ready) is not int or type(total) is not int or ready < 0 or total < 0:
        raise ValueError("AI Studio status response has invalid account counters")
    if ready < 1:
        raise ValueError("AI Studio has no ready Google account; import a signed-in session first")
    return {"state": state, "readyAccounts": ready, "totalAccounts": total}


def aistudio_console_status(values, timeout=20):
    """Log in with AISTUDIO_ADMIN_PASSWORD and inspect /api/status without exposing the password."""
    password = values.get("AISTUDIO_ADMIN_PASSWORD")
    if not password:
        raise ValueError("AI Studio admin password is missing; run install.sh to create AISTUDIO_ADMIN_PASSWORD")
    username = values.get("AISTUDIO_ADMIN_USER") or "admin"
    base = service_url("aistudio", values).rstrip("/")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    body = json.dumps({"username": username, "password": password}).encode()
    login = urllib.request.Request(base + "/api/auth/login", data=body,
                                   headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(login, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError("AI Studio admin login failed; check AISTUDIO_ADMIN_USER/AISTUDIO_ADMIN_PASSWORD") from None
        raise
    with opener.open(base + "/api/status", timeout=timeout) as response:
        try:
            data = json.load(response)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("AI Studio status response must be valid JSON") from None
    return aistudio_account_status(data)


def wait_service(name, values, seconds=180):
    base = service_url(name, values)
    if name == "aistudio":
        # The Playground proxy exposes an SPA root and no /health endpoint.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with HTTP.open(base + "/", timeout=3) as response:
                    if response.status < 500:
                        return
            except urllib.error.HTTPError as error:
                if error.code < 500:
                    return
            except OSError:
                pass
            time.sleep(1)
        raise ValueError(f"aistudio did not answer at {base} within {seconds}s; inspect compose logs")
    path = "/healthz" if name == "workbuddy" else "/health"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(base + path)
            with HTTP.open(req, timeout=3) as response:
                raw = response.read()
                data = json.loads(raw)
                if (name == "workbuddy" and data.get("service") == "workbuddy2api") or data.get("status") == "ok":
                    return
        except urllib.error.HTTPError as error:
            if name == "workbuddy" and error.code == 503:
                data = json.load(error)
                if data.get("service") == "workbuddy2api":
                    print("WorkBuddy is listening; add an account in /panel/ to make it schedulable.", flush=True)
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise ValueError(f"{name} did not become ready at {base} within {seconds}s; inspect compose logs")


def enable_antigravity(values):
    base = service_url("antigravity", values)
    key = values["ANTIGRAVITY_WEB_PASSWORD"]
    config = request(base + "/api/config", key)
    config["proxy"].update(enabled=True, auto_start=True, allow_lan_access=True)
    if values.get("UPSTREAM_PROXY"):
        config["proxy"]["upstream_proxy"] = {"enabled": True, "url": values["UPSTREAM_PROXY"]}
    request(base + "/api/config", key, {"config": config})
    request(base + "/api/proxy/start", key, {})
    state = request(base + "/api/proxy/status", key)
    print(f"Antigravity API enabled; {state.get('active_accounts', 0)} active account(s).", flush=True)


def verify_generation(base, key, model, stream=False):
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly PROXY_KIT_OK and nothing else."}],
               "max_tokens": 2048, "stream": stream}
    if stream:
        req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        parts, done = [], False
        with HTTP.open(req, timeout=120) as response:
            for line in response:
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    done = True
                    break
                event = json.loads(raw)
                if "error" in event:
                    raise ValueError("Upstream returned an error inside the stream")
                for choice in event.get("choices", []):
                    parts.append(choice.get("delta", {}).get("content") or "")
        if not done:
            raise ValueError("Generation stream ended without [DONE]")
        content = "".join(parts)
    else:
        data = request(base + "/v1/chat/completions", key, payload, timeout=120)
        content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
    if content.strip() != "PROXY_KIT_OK":
        raise ValueError("Generation did not return the verification marker (check model quota and output budget)")


def check(name, values, model=None, stream=False):
    wait_service(name, values, seconds=10)
    if name == "sub2api":
        if model:
            raise ValueError("Use connect.sh --provider aistudio --test --model to verify Sub2API routed generation")
        print("sub2api health: PASS (use scripts/connect-sub2api.py --test to test routed generation)")
        return
    key = values[name.upper() + "_API_KEY"]
    base = service_url(name, values)
    try:
        request(base + "/v1/models")
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise ValueError(f"{name} expected HTTP 401 without credentials; got {error.code}") from None
    else:
        raise ValueError(f"{name} did not reject an unauthenticated model-list request")
    models = request(base + "/v1/models", key).get("data", [])
    if not models:
        raise ValueError(f"{name} returned an empty model list; authorize an account first")
    if model and model not in {item["id"] for item in models}:
        raise ValueError(f"{name} does not advertise the requested model {model}")
    print(f"{name}: auth PASS; {len(models)} model(s).", flush=True)
    if name == "aistudio" and model:
        summary = aistudio_console_status(values)
        print(f"AI Studio console: {summary['readyAccounts']}/{summary['totalAccounts']} account(s) ready "
              f"({summary['state']}).", flush=True)
    if model:
        verify_generation(base, key, model, stream)
        print(f"{name}: real {'streaming' if stream else 'non-streaming'} generation PASS (PROXY_KIT_OK)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "start", "stop", "status", "check", "access"))
    parser.add_argument("service", nargs="?", default="all", choices=(*SERVICES, "all"))
    parser.add_argument("--env-file", type=pathlib.Path, default=ROOT / "runtime/stack.env")
    parser.add_argument("--prepare-only", action="store_true", help="Generate configuration without Docker or network changes")
    parser.add_argument("--install-docker", action="store_true", help="Handled by the Linux install.sh bootstrap")
    parser.add_argument("--skip-pull", action="store_true", help="Require cached images; do not contact registries")
    parser.add_argument("--workbuddy-env-proxy", action="store_true", help="Build the pinned WorkBuddy transport fix for HTTP(S)_PROXY support")
    parser.add_argument("--import-antigravity", type=pathlib.Path, help="Reuse API_KEY/WEB_PASSWORD from a previous deployment env")
    parser.add_argument("--antigravity-data", type=pathlib.Path, help="Reuse an existing Manager account data directory")
    parser.add_argument("--import-workbuddy", type=pathlib.Path, help="Copy auths/*.json from an existing WorkBuddy install")
    parser.add_argument("--upstream-proxy", help="Optional outbound proxy reachable from the containers")
    parser.add_argument("--model", help="check: additionally make a real generation request")
    parser.add_argument("--stream", action="store_true", help="check: verify content and [DONE] in a real SSE response")
    for name in SERVICES:
        parser.add_argument("--" + name + "-port", type=int)
    args = parser.parse_args()
    args.env_file = args.env_file.expanduser().resolve()
    services = list(SERVICES) if args.service == "all" else [args.service]
    values = initialize(args) if args.command == "install" else read_env(args.env_file)
    if not values:
        raise ValueError("Run bash install.sh first")
    if args.prepare_only:
        if args.command != "install":
            raise ValueError("--prepare-only is only valid for installation")
        return
    if args.workbuddy_env_proxy:
        if args.command != "install" or "workbuddy" not in services:
            raise ValueError("--workbuddy-env-proxy requires install workbuddy or install all")
        run([sys.executable, str(ROOT / "tools/build-workbuddy-proxy.py"), "--env-file", str(args.env_file)])
        values = read_env(args.env_file)
    if args.command == "check":
        for name in services:
            check(name, values, args.model, args.stream)
        return
    if args.command == "access":
        write_local_access(values)
        print(ROOT / "runtime/open-aistudio.html")
        return
    docker = docker_command()
    compose(docker, args, values, "config", "--quiet")
    if args.command in ("install", "start"):
        ensure_images(docker, args, values, services)
        compose(docker, args, values, "up", "-d", "--no-build", "--pull", "never", *services)
        for name in services:
            wait_service(name, values)
        if "antigravity" in services:
            enable_antigravity(values)
        write_local_access(values)
        for name in services:
            print(f"{name}: listening on 0.0.0.0:{values[name.upper() + '_PORT']}; local URL {service_url(name, values)}", flush=True)
        print(f"Credentials stay in {args.env_file}; no secrets are printed.", flush=True)
    elif args.command == "stop":
        dependencies = ["postgres", "redis"] if "sub2api" in services else []
        compose(docker, args, values, "stop", *services, *dependencies)
    else:
        compose(docker, args, values, "--profile", "*", "ps", "--all")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        print(f"ERROR: Gateway API returned HTTP {error.code} for {error.url}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
