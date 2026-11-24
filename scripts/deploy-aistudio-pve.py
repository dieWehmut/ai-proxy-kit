#!/usr/bin/env python3
"""Deploy only AI Studio beside an existing Sub2API LXC, preserving its stack."""

import argparse
import base64
import json
import pathlib
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Compile this program locally before transmitting it. Secrets remain stdin
# data; they are never interpolated into either the PVE or LXC shell command.
REMOTE_INSTALL = r"""
import base64, datetime, io, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile

os.umask(0o077)
payload = json.loads(sys.stdin.read())
base = pathlib.Path(payload['directory'])
if not base.is_absolute():
    raise ValueError('The deployment directory must be absolute')
if base.exists() and not (base / '.deployment-source').exists():
    raise RuntimeError('Refusing to overwrite an unrelated existing directory')

machine = subprocess.run(['uname', '-m'], capture_output=True, text=True, check=True).stdout.strip()
expected_arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine)
image = subprocess.run(['docker', 'image', 'inspect', payload['image']], capture_output=True, text=True, check=True)
image_data = json.loads(image.stdout)[0]
if image_data['Architecture'] != expected_arch:
    raise RuntimeError('AI Studio image architecture does not match the LXC')
subprocess.run(['docker', 'network', 'inspect', payload['network']], capture_output=True, check=True)

base.mkdir(mode=0o700, parents=True, exist_ok=True)
backup = pathlib.Path('/root/codex-aistudio-backups') / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup.mkdir(mode=0o700, parents=True)
inventory = subprocess.run(['docker', 'ps', '--format', '{{json .Names}} {{json .ID}} {{json .Status}}'], capture_output=True, text=True, check=True)
(backup / 'containers-before.txt').write_text(inventory.stdout)
(base / '.deployment-source').write_text(payload['source'] + chr(10))

with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload['archive']))) as archive:
    for member in archive.getmembers():
        target = (base / member.name).resolve()
        if not target.is_relative_to(base.resolve()) or not (member.isdir() or member.isfile()):
            raise RuntimeError('Unsafe source archive member')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as source, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as pending:
            shutil.copyfileobj(source, pending)
            temporary = pathlib.Path(pending.name)
        temporary.chmod(member.mode & 0o755)
        temporary.replace(target)

(base / 'runtime').mkdir(mode=0o700, exist_ok=True)
env_file = base / 'runtime/stack.env'
if env_file.exists():
    (backup / 'stack-env-before').write_bytes(env_file.read_bytes())
else:
    env_file.write_text(payload['env'])
    env_file.chmod(0o600)

sys.path.insert(0, str(base / 'lib'))
import proxykit
values = proxykit.read_env(env_file)
values['LOCAL_UID'] = str(os.getuid())
values['LOCAL_GID'] = str(os.getgid())
values['ANTIGRAVITY_DATA_DIR'] = str(base / 'runtime/antigravity')
values['SUB2API_DOCKER_NETWORK'] = payload['network']
if payload.get('hydrogen_only'):
    values['AISTUDIO_HYDROGEN_ONLY'] = 'true'
proxykit.save_env(env_file, values)
for folder in ['aistudio/auth', 'aistudio/data']:
    (base / 'runtime' / folder).mkdir(mode=0o700, parents=True, exist_ok=True)

command = ['docker', 'compose', '--project-name', 'ai-proxy-kit-aistudio', '--project-directory', str(base), '--env-file', str(env_file), '-f', str(base / 'compose.yml'), '-f', str(base / 'compose.aistudio-sub2api.yml')]
hydrogen_only = values.get('AISTUDIO_HYDROGEN_ONLY') == 'true'
if hydrogen_only:
    command += ['-f', str(base / 'compose.aistudio-hydrogen.yml')]
command += ['--profile', 'aistudio']
render = subprocess.run(command + ['config', '--format', 'json'], capture_output=True, text=True, env=dict(os.environ, **values), check=True)
model = json.loads(render.stdout)
if set(model['services']) != {'aistudio'}:
    raise RuntimeError('The deployment must contain only the AI Studio service')
if not model['networks']['sub2api']['external'] or model['networks']['sub2api']['name'] != payload['network']:
    raise RuntimeError('AI Studio must join the existing Sub2API network')
if hydrogen_only and model['services']['aistudio']['command'] != ['node', '--require', '/app/proxy-kit/preload.cjs', 'main.js']:
    raise RuntimeError('The Hydrogen adapter must be active')

result = subprocess.run(command + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'aistudio'], capture_output=True, text=True, env=dict(os.environ, **values), timeout=90)
error = result.stderr.strip()
for name, value in values.items():
    if any(word in name for word in ['PASSWORD', 'SECRET', 'TOKEN', 'KEY']) and len(value) > 8:
        error = error.replace(value, '<redacted>')
receipt = {'directory': str(base), 'backup': str(backup), 'source': payload['source'], 'image': payload['image'], 'image_architecture': image_data['Architecture'], 'hydrogen_only': hydrogen_only, 'compose_rc': result.returncode, 'output': result.stdout.strip(), 'error': error[:1200]}
(backup / 'deployment-result.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt), flush=True)
raise SystemExit(result.returncode)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="PVE SSH destination, e.g. root@100.104.54.2")
    parser.add_argument("--ssh", default="ssh", help="SSH executable; Windows OpenSSH may be used from WSL")
    parser.add_argument("--host-key-alias", help="Existing known-host identity for this same PVE host")
    parser.add_argument("--vmid", type=int, default=158)
    parser.add_argument("--directory", default="/opt/ai-proxy-kit-aistudio")
    parser.add_argument("--network", default="sub2api_sub2api-network")
    parser.add_argument("--env-file", type=pathlib.Path, default=ROOT / "runtime/stack.env")
    parser.add_argument("--hydrogen-only", action="store_true", help="Register and strictly route only gemini-4-hydrogen")
    args = parser.parse_args()
    compile(REMOTE_INSTALL, "aistudio-lxc-install", "exec")
    sys.path.insert(0, str(ROOT / "lib"))
    import proxykit
    values = proxykit.read_env(args.env_file)
    if not values.get("AISTUDIO_API_KEY") or not values.get("AISTUDIO_WEB_PASSWORD"):
        raise ValueError("Run install.sh aistudio --prepare-only before deployment")
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    payload = {"archive": base64.b64encode(archive).decode(), "env": args.env_file.read_text(), "source": source, "directory": args.directory, "network": args.network, "image": values["AISTUDIO_IMAGE"], "hydrogen_only": args.hydrogen_only}
    wrapper = "import subprocess,sys\nresult=subprocess.run(['pct','exec'," + repr(str(args.vmid)) + ", '--','python3','-c'," + repr(REMOTE_INSTALL) + "],input=sys.stdin.read(),text=True,capture_output=True)\nprint(result.stdout)\nsys.stderr.write(result.stderr)\nraise SystemExit(result.returncode)"
    compile(wrapper, "aistudio-pve-forwarder", "exec")
    command = [args.ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if args.host_key_alias:
        command += ["-o", "HostKeyAlias=" + args.host_key_alias]
    command += [args.host, "python3 -c " + shlex.quote(wrapper)]
    result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=180)
    print(result.stdout.strip())
    if result.returncode:
        print("Deployment did not complete; inspect its reported error.", file=sys.stderr)
        sys.stderr.write(result.stderr[-1600:])
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
