#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    if command -v python3 >/dev/null; then
        exec python3 "$root/lib/proxykit.py" install --help
    fi
    echo 'Usage: bash install.sh [all|antigravity|workbuddy|sub2api|aistudio] [--prepare-only] [--install-docker]'
    exit 0
fi
as_root() {
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
    if command -v apt-get >/dev/null; then
        as_root apt-get update
        as_root apt-get install -y python3 curl ca-certificates
    elif command -v dnf >/dev/null; then
        as_root dnf install -y python3 curl ca-certificates
    else
        echo 'Install Python 3 and curl with your package manager, then rerun.' >&2
        exit 1
    fi
fi
for argument in "$@"; do
    if [[ $argument == --install-docker ]]; then
        if ! command -v docker >/dev/null; then
            installer=$(mktemp)
            trap 'rm -- "$installer"' EXIT
            curl -fsSL --connect-timeout 15 --max-time 120 https://get.docker.com -o "$installer"
            as_root sh "$installer"
        fi
        if command -v systemctl >/dev/null; then
            as_root systemctl start docker
        fi
    fi
done
exec python3 "$root/lib/proxykit.py" install "$@"
