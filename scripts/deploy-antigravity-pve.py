#!/usr/bin/env python3
"""Deploy only Antigravity beside an existing Sub2API LXC."""

import argparse
import base64
import json
import pathlib
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

REMOTE_INSTALL = r"""
import base64, datetime, io, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile

os.umask(0o077)
payload = json.load(sys.stdin)
base = pathlib.Path(payload['directory'])
if not base.is_absolute() or base == pathlib.Path('/'):
    raise ValueError('The deployment directory must be an absolute application directory')
if base.exists() and not (base / '.deployment-source').exists():
    raise RuntimeError('Refusing to overwrite an unrelated existing directory')

machine = subprocess.run(['uname', '-m'], capture_output=True, text=True, check=True).stdout.strip()
architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine)
image = json.loads(subprocess.run(['docker', 'image', 'inspect', payload['image']], capture_output=True, text=True, check=True).stdout)[0]
if not architecture or image['Architecture'] != architecture:
    raise RuntimeError('The published image architecture must match the LXC')
subprocess.run(['docker', 'network', 'inspect', payload['network']], capture_output=True, check=True)

def inventory():
    rows = subprocess.run(['docker', 'ps', '--no-trunc', '--format', '{{.Names}} {{.ID}}'], capture_output=True, text=True, check=True).stdout
    return dict(row.split(' ', 1) for row in rows.splitlines())

before = inventory()
base.mkdir(mode=0o700, parents=True, exist_ok=True)
backup = pathlib.Path('/root/codex-antigravity-backups') / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup.mkdir(mode=0o700, parents=True)
(backup / 'containers-before.json').write_text(json.dumps(before, indent=2))
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

runtime = base / 'runtime'
runtime.mkdir(mode=0o700, exist_ok=True)
env_path = runtime / 'stack.env'
if env_path.exists():
    (backup / 'stack-env-before').write_bytes(env_path.read_bytes())
else:
    env_path.write_text(payload['env'])
env_path.chmod(0o600)
sys.path.insert(0, str(base / 'lib'))
import proxykit
values = proxykit.read_env(env_path)
values.update(LOCAL_UID=str(os.getuid()), LOCAL_GID=str(os.getgid()),
              ANTIGRAVITY_DATA_DIR=str(runtime / 'antigravity'),
              SUB2API_DOCKER_NETWORK=payload['network'], UPSTREAM_PROXY=payload['upstream_proxy'])
proxykit.save_env(env_path, values)

data = runtime / 'antigravity'
data.mkdir(mode=0o700, exist_ok=True)
accounts = data / 'accounts'
accounts.mkdir(mode=0o700, exist_ok=True)
if not (data / 'accounts.json').exists():
    # Import only account records, never local logs, telemetry, or SQLite stores.
    for name, content in payload.get('accounts', {}).items():
        target = data / name
        if name != 'accounts.json' and (target.parent != accounts or target.suffix != '.json'):
            raise RuntimeError('Unexpected account import path')
        target.write_text(content)
        target.chmod(0o600)

gui_path = data / 'gui_config.json'
gui = json.loads(gui_path.read_text()) if gui_path.exists() else payload['gui_config']
if gui_path.exists():
    (backup / 'gui-config-before.json').write_bytes(gui_path.read_bytes())
if gui is not None:
    gui['proxy'].update(enabled=True, allow_lan_access=True, auto_start=True, auth_mode='all_except_health',
                        port=8045, api_key=values['ANTIGRAVITY_API_KEY'], admin_password=values['ANTIGRAVITY_WEB_PASSWORD'],
                        upstream_proxy={'enabled': bool(values['UPSTREAM_PROXY']), 'url': values['UPSTREAM_PROXY']})
    gui_path.write_text(json.dumps(gui, indent=2))
    gui_path.chmod(0o600)

command = ['docker', 'compose', '--project-name', 'ai-proxy-kit-antigravity', '--project-directory', str(base),
           '--env-file', str(env_path), '-f', str(base / 'compose.yml'),
           '-f', str(base / 'compose.antigravity-sub2api.yml'), '--profile', 'antigravity']
render = json.loads(subprocess.run(command + ['config', '--format', 'json'], capture_output=True, text=True, check=True).stdout)
if set(render['services']) != {'antigravity'}:
    raise RuntimeError('The deployment must start only Antigravity')
network = render['networks']['sub2api']
if not network['external'] or network['name'] != payload['network']:
    raise RuntimeError('Antigravity must join the existing Sub2API network')

result = subprocess.run(command + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'antigravity'], capture_output=True, text=True, timeout=90)
after = inventory()
preserved = all(after.get(name) == identity for name, identity in before.items() if name != 'antigravity-manager')
error = result.stderr
for name, value in values.items():
    if any(word in name for word in ['PASSWORD', 'SECRET', 'TOKEN', 'KEY']) and len(value) > 8:
        error = error.replace(value, '<redacted>')
receipt = {'source': payload['source'], 'directory': str(base), 'backup': str(backup),
           'image': image['Id'], 'architecture': architecture, 'network': payload['network'],
           'compose_rc': result.returncode, 'other_containers_preserved': preserved, 'error': error.strip()[:1200]}
(backup / 'deployment-result.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt), flush=True)
if result.returncode or not preserved:
    raise SystemExit(result.returncode or 1)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', help='PVE SSH destination')
    parser.add_argument('--ssh', default='ssh')
    parser.add_argument('--host-key-alias')
    parser.add_argument('--vmid', type=int, default=158)
    parser.add_argument('--directory', default='/opt/ai-proxy-kit-antigravity')
    parser.add_argument('--network', default='sub2api_sub2api-network')
    parser.add_argument('--upstream-proxy', default='')
    parser.add_argument('--env-file', type=pathlib.Path, default=ROOT / 'runtime/stack.env')
    parser.add_argument('--account-data', type=pathlib.Path, help='Existing private Antigravity account directory')
    args = parser.parse_args()
    if args.vmid in (152, 153):
        parser.error('LXC 152 and 153 are clients; reverse proxies must be deployed elsewhere')
    compile(REMOTE_INSTALL, 'antigravity-lxc-install', 'exec')
    sys.path.insert(0, str(ROOT / 'lib'))
    import proxykit
    values = proxykit.read_env(args.env_file)
    if not values.get('ANTIGRAVITY_API_KEY') or not values.get('ANTIGRAVITY_WEB_PASSWORD'):
        parser.error('Run install.sh antigravity --prepare-only before deployment')
    if args.account_data:
        account_root = args.account_data.expanduser().resolve()
        gui = json.loads((account_root / 'gui_config.json').read_text())
        accounts = {str(path.relative_to(account_root)): path.read_text()
                    for path in [account_root / 'accounts.json', *sorted((account_root / 'accounts').glob('*.json'))]}
    else:
        # The official app creates its complete default config on first start.
        gui = None
        accounts = {}
    archive = subprocess.run(['git', 'archive', '--format=tar', 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout
    source = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    payload = {'archive': base64.b64encode(archive).decode(), 'source': source, 'env': args.env_file.read_text(),
               'directory': args.directory, 'network': args.network, 'upstream_proxy': args.upstream_proxy,
               'image': values['ANTIGRAVITY_IMAGE'], 'gui_config': gui, 'accounts': accounts}
    wrapper = 'import subprocess,sys\nr=subprocess.run(' + repr(['pct', 'exec', str(args.vmid), '--', 'python3', '-c', REMOTE_INSTALL]) + ',input=sys.stdin.read(),capture_output=True,text=True)\nsys.stdout.write(r.stdout);sys.stderr.write(r.stderr);raise SystemExit(r.returncode)'
    command = [args.ssh, '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
    if args.host_key_alias:
        command += ['-o', 'HostKeyAlias=' + args.host_key_alias]
    command += [args.host, 'python3 -c ' + shlex.quote(wrapper)]
    result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=180)
    print(result.stdout.strip())
    if result.returncode:
        sys.stderr.write(result.stderr[-1200:])
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
