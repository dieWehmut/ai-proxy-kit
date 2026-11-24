# Antigravity beside the existing Sub2API service

LXC 158 hosts the reverse proxy. LXC 152 and 153 remain OpenClaw clients.
This deployment uses the published `lbjlaq/antigravity-manager:v5.0.3` image;
the image must already be loaded for the LXC's architecture.

```bash
bash install.sh antigravity --prepare-only
python3 scripts/deploy-antigravity-pve.py root@pve-host \
  --vmid 158 --network sub2api_sub2api-network \
  --upstream-proxy http://reachable-proxy:7890
```

The installer starts only the Antigravity Compose profile and joins the existing
Sub2API Docker network. It preserves the container identities of Sub2API,
PostgreSQL, Redis, and WorkBuddy, and refuses to deploy a proxy into 152 or 153.
Accounts can be added manually through the WebUI after deployment. Optional
`--account-data` imports only `accounts.json` and `accounts/*.json`; existing
accounts are retained on subsequent runs. API and WebUI credentials remain in
the private deployment directory's `runtime/stack.env`.

With the default port, the LAN WebUI is `http://lxc158-host:8045/`, and the
OpenAI Base URL is `http://lxc158-host:8045/v1`. Sub2API can reach the service
inside Docker at `http://antigravity:8045`.

Create a standard **OpenAI** group named `antigravity` with its model allowlist
disabled, then bind an API Key account to that group. Its API key comes from
`ANTIGRAVITY_API_KEY`; its base URL is `http://antigravity:8045`. Synchronize
the available upstream models into the account mapping. This group keeps
multiple models and is not assigned to either robot's Key.

The connector reads the live upstream model catalog and creates or updates this
group and account. It allows new model names through OpenAI passthrough and does
not change the robot keys. Run it on LXC 158 with a private administrator token:

```bash
python3 scripts/connect-antigravity-sub2api.py \
  --env-file /opt/ai-proxy-kit-antigravity/runtime/stack.env \
  --admin-token-file /path/to/private/admin-token --manual-accounts
```

Check `/health`, the WebUI, an authenticated `/v1/models` request, and a real
generation before declaring the service ready. Compare `X-Upstream-Model` with
the requested model and verify a request through Sub2API using the new group's
Key. Health and model discovery alone do not establish upstream account access.
If the operator will add accounts manually, validate the deployment and routing
first and leave the upstream generation test until an account is configured.
An empty account store can leave the proxy API returning 503 while the WebUI
remains available. Add accounts and start the proxy in the WebUI, then rerun the
connector to update the visible model catalog. Requests remain unrestricted by
the group model allowlist or the account model filter.
