#!/bin/sh
set -u

/app/antigravity-tools --headless "$@" &
antigravity_pid=$!

stop_service() {
    kill -TERM "$antigravity_pid" 2>/dev/null || true
    wait "$antigravity_pid" 2>/dev/null || true
    exit 0
}
trap stop_service TERM INT

attempt=0
while [ "$attempt" -lt 60 ]; do
    kill -0 "$antigravity_pid" 2>/dev/null || break
    if curl --silent --fail http://127.0.0.1:8045/health >/dev/null; then
        proxy_config=$(curl --silent --fail \
            -H "Authorization: Bearer ${WEB_PASSWORD:-${API_KEY:-}}" \
            http://127.0.0.1:8045/api/config 2>/dev/null) || proxy_config=''
        if printf '%s' "$proxy_config" | grep -Eq '"auto_start"[[:space:]]*:[[:space:]]*true'; then
            if ! curl --silent --show-error --fail -X POST \
                -H "Authorization: Bearer ${WEB_PASSWORD:-${API_KEY:-}}" \
                http://127.0.0.1:8045/api/proxy/start >/dev/null; then
                echo 'Antigravity API auto-start failed; see the WebUI proxy status.' >&2
            fi
        fi
        unset proxy_config
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

wait "$antigravity_pid"
