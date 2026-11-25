# OpenClaw `sub2api-auto` 插件部署 Runbook（新 LXC bot）

适用：在**新的 LXC 机器人**上部署 `sub2api-auto`（`@ai-proxy-kit/openclaw-sub2api` ≥ 0.1.1）并完成验收。
参照实例：LXC 152、LXC 153（两实例各用独立 Sub2API Key）。
关联代码：`integrations/openclaw-sub2api/{install.mjs,index.mjs,group-sync.mjs}`；行为说明见 `docs/OPENCLAW_SUB2API_MODELS_FALLBACK.md`。

> **安全红线**：命令输出、日志、备份、文档一律不得出现 Key 原文。下文 Key 只用 `<BOT_KEY>` 占位；
> 排错只看 HTTP 状态码和非敏感元数据。状态文件只存 Key 的 SHA-256 指纹。

## 0. 两台参考机的差异

| 主机 | Node 目录（`PATH` 前缀） | OpenClaw 入口 | 备注 |
| --- | --- | --- | --- |
| 152 | `/root/.nvm/versions/node/v26.13.1/bin` | 该目录 `../lib/node_modules/openclaw/dist/index.js` | 新版，支持 `hooks.allowConversationAccess` |
| 153 | `/root/.nvm/versions/node/v22.22.3/bin` | 同上 | OpenClaw 2026.5.22；**无 journald**，`systemctl status` 是唯一日志源 |

网关是 **systemd user unit**（`openclaw-gateway`），但以 root 身份运行，因此**每条** systemctl 都必须带
`XDG_RUNTIME_DIR=/run/user/0`，否则会报 `Failed to connect to bus`，或看到"服务未运行"的假象。

## 1. 前置检查

```bash
export PATH=/root/.nvm/versions/node/v26.13.1/bin:$PATH   # 153 改成 /root/.nvm/versions/node/v22.22.3/bin
node -v && command -v node && openclaw --version          # Node 必须 >= 22
XDG_RUNTIME_DIR=/run/user/0 systemctl --user status openclaw-gateway -n 20 --no-pager | head -5
ls -l /root/.openclaw/openclaw.json /root/.openclaw/.env
grep -c '^SUB2API_API_KEY=' /root/.openclaw/.env          # 期望 1；只看计数，不打印值
jq -r '.agents.defaults.model.primary' /root/.openclaw/openclaw.json   # 期望 sub2api/<别名>
```

四项必须同时成立，否则先修环境再装：Node ≥ 22；网关 unit 可见且 active；`.env` 含
`SUB2API_API_URL` 与 `SUB2API_API_KEY`；`primary` 已是 `sub2api/*`（安装器会拒绝非 Sub2API profile）。

## 2. 用官方安装器安装

**先做第 4 节的备份**，再执行本节。

```bash
cd /path/to/ai-proxy-kit-openclaw-sync        # 仓库根目录
export PATH=/root/.nvm/versions/node/v26.13.1/bin:$PATH
node integrations/openclaw-sub2api/install.mjs --profile /root/.openclaw
```

安装器做这些事（全部原子写入、`0600`/`0700`）：

- 读 `.env` 的 `SUB2API_API_URL` + `SUB2API_API_KEY`，用该 Key 发现所属分组模型；
- 迁移并删除 `.env` 中的 `SUB2API_MODEL`、`ACTIVE_MODEL_PROVIDER`、`ACTIVE_MODEL_ID`；
- 写 `plugins.entries.sub2api-auto`（`enabled` + `config.baseUrl/keyEnv/configPath/statePath/refreshIntervalMs/timeoutMs`）；
- 把插件目录追加进 `plugins.load.paths`（必要时也补 `plugins.allow`）；
- 写 `plugins.entries.sub2api-auto.hooks.allowConversationAccess = true`（旧版 OpenClaw 不认该字段时自动省略）；
- 用**实际安装的 CLI** 对临时配置跑 `openclaw config validate --json`，通过后才落盘；
- 备份到 `/root/openclaw-config-backups/sub2api-auto-<ISO 时间>-XXXX/`（含 `installation.json` 回执）。

可选参数：`--node <node>`、`--openclaw-entry <dist/index.js>`、`--plugin <插件目录>`、`--backup-root <目录>`。
输出 JSON 里 `restart_required: true`，所以必须做第 5 节。

## 3. 手工替换插件文件（真实踩过的坑）

`tar -xzf` 会保留归档内的 uid/gid。若归档是普通用户打的包，解出来的插件文件属主就是 `1000` 之类。
**OpenClaw 加载插件时校验属主，uid ≠ 0 直接拒绝**，日志只有一行
`blocked plugin candidate: suspicious ownership`；而 `systemctl status` 仍显示 `active`、
`openclaw.json` 里的插件条目也完好 —— 极易误判成"配置没生效 / 插件没注册 / 模型没切换"。

正确姿势（在网关实际加载的目录执行，路径必须等于 `plugins.load.paths` 里的那一项）：

```bash
PLUGIN_DIR=/root/.openclaw/plugins          # 必须与 openclaw.json 的 plugins.load.paths 一致
cd "$PLUGIN_DIR"
tar --no-same-owner -xzf /root/openclaw-sub2api-0.1.1.tar.gz
cd openclaw-sub2api
chown -R root:root .
chmod 600 *.mjs *.json
ls -ln .                                    # 第三、四列必须全是 0 0（root:root）
```

`--no-same-owner` 让解包归属当前用户，`chown -R` 兜底修正历史残留，`chmod 600` 收掉组/其他用户读权限。
改完必须重启网关（第 5 节），否则旧进程仍持有旧文件。

## 4. 备份（任何改动之前）

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/root/.openclaw/backups/sub2api-auto-$TS
mkdir -p "$BK" && chmod 700 "$BK"
cp -a /root/.openclaw/openclaw.json "$BK"/
cp -a /root/.openclaw/.env "$BK"/ 2>/dev/null || true
cp -a /root/.openclaw/state/sub2api-auto.json "$BK"/ 2>/dev/null || true
cp -a /root/.openclaw/plugins/openclaw-sub2api "$BK"/ 2>/dev/null || true
chmod -R go-rwx "$BK"; echo "$BK"      # 记下这个路径，回滚要用
```

安装器自己也会备份一份到 `/root/openclaw-config-backups/`，但**手工备份是回滚的第一依据**，两份都留。

## 5. 重启与验收

```bash
XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart openclaw-gateway
sleep 25        # 后台刷新周期 15s + 启动余量，必须 >= 20s
XDG_RUNTIME_DIR=/run/user/0 systemctl --user status openclaw-gateway -n 200 --no-pager
XDG_RUNTIME_DIR=/run/user/0 systemctl --user status openclaw-gateway -n 200 --no-pager | grep -n 'plugins:'
jq -r '[.checked_at, .generation, .model.id] | @tsv' /root/.openclaw/state/sub2api-auto.json
date -u +%FT%TZ
```

三条通过标准：

1. 启动行里能看到 `plugins: ... sub2api-auto`（缺失 = 插件未加载，先查第 3 节的属主）；
2. 状态文件 `checked_at` 是**本次重启之后**的时间，`generation` 单调递增；
3. `status` 里没有 `blocked plugin candidate`。153 无 journald，`status -n 200` 就是全部日志，别去找 `journalctl`。

## 6. 冒烟测试

```bash
export PATH=/root/.nvm/versions/node/v26.13.1/bin:$PATH   # 153 用 v22.22.3
openclaw agent --agent main --session-id "conv-check-$(date +%s)" -m "只回复 OK" --json | jq -r '.stopReason'
XDG_RUNTIME_DIR=/run/user/0 systemctl --user status openclaw-gateway -n 200 --no-pager | grep 'model-fetch' | tail -3
```

期望：`stopReason` 为 `stop`（不是 `error`/`aborted`）；网关日志出现
`model-fetch ... model=<分组唯一模型> status=200`。若 `model=` 与状态文件 `model.id` 不一致，
按第 8 节定位。每次换新 `--session-id`，避免复用旧会话掩盖问题。

## 7. 回滚

```bash
BK=/root/.openclaw/backups/sub2api-auto-<第 4 节输出的时间戳>
cp -a "$BK/openclaw.json" /root/.openclaw/openclaw.json
cp -a "$BK/.env" /root/.openclaw/.env
cp -a "$BK/sub2api-auto.json" /root/.openclaw/state/sub2api-auto.json
[ -d "$BK/openclaw-sub2api" ] && { rm -rf /root/.openclaw/plugins/openclaw-sub2api; cp -a "$BK/openclaw-sub2api" /root/.openclaw/plugins/; }
chown -R root:root /root/.openclaw/plugins /root/.openclaw/state
chmod 700 /root/.openclaw/state && chmod 600 /root/.openclaw/openclaw.json /root/.openclaw/.env /root/.openclaw/state/sub2api-auto.json
XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart openclaw-gateway
sleep 25
XDG_RUNTIME_DIR=/run/user/0 systemctl --user status openclaw-gateway -n 50 --no-pager | grep -E 'blocked plugin|plugins:'
```

只回滚配置、不动插件文件时跳过第 4 行；只修属主时只跑 `chown`/`chmod` 两行再重启。
安装器回执 `/root/openclaw-config-backups/sub2api-auto-*/installation.json` 里的 `backup` 字段指向它自己的备份。

## 8. 常见故障对照表

| 现象（日志/报错关键字） | 根因 | 处置 |
| --- | --- | --- |
| `blocked plugin candidate: suspicious ownership` | 插件文件属主非 root（uid ≠ 0），网关仍显示 active | 第 3 节：`tar --no-same-owner` → `chown -R root:root .` → `chmod 600` → 重启 |
| `non-unique-model` 持续出现 | Key 未绑定单模型分组（`/v1/models` 返回 ≥2 个 id） | 权威歧义，插件按设计不覆盖；把该 Key 归入单模型分组 |
| `http-404` + `keeping last available model` | 旧版插件（< 0.1.1）缺 `/models` 回退，且 key-group 端点未打补丁 | 升级插件到 ≥ 0.1.1 后重装、重启 |
| `rate_limit` / HTTP `429` | Sub2API 上游配额或限流 | 与插件无关，查上游用量/分组配额，勿改插件配置 |
| `model_not_found` | 配置 `primary` 与当前分组模型不一致 | 比对状态文件 `model.id` 与 `agents.defaults.model.primary`，重跑安装器 |
| `checked_at` 不更新 | 后台刷新失败（网络、锁、目录权限） | 查 `state/` 属主与 `0700`，看 `.lock`/`.sequence.json` 是否可写 |

错误码速查：`invalid-schema`、`missing-group`、`inactive-group`、`inconsistent-group`、`non-unique-model`、
`invalid-model`、`unsupported-platform`、`http-<code>`、`discovery-unavailable`、`superseded-identity`、
`coordination-*`、`persistence-*`。判定只看错误码与状态码，**不要**打印响应体（可能含凭据）。
