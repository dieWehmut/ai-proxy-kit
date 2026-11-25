# LXC 158 AI Studio Playground 部署与验收手册

> AI Studio 的当前生成上游是 Compose 服务 **`aistudio`**，镜像为
> `ghcr.io/mag1cfall/aistudio2api:latest`，容器端口为 **`2048`**，通道为
> **`playground`**。旧 Build 通道已经停用。
>
> LXC158 现有部署继续使用宿主端口 **`7870`**。这是宿主机端口差异：
> `0.0.0.0:7870 -> 容器 2048`，不是第二个 AI Studio 服务，也不是容器监听 7870。
> 同一 Compose 网络内必须访问 `http://aistudio:2048`；只有跨栈或宿主机客户端才使用
> `http://127.0.0.1:7870` 或当前环境的宿主机容器网关地址。

本文面向 PVE 节点上的 LXC158，部署目录固定为 `/opt/ai-proxy-kit-aistudio`，
通过 `pct exec 158` 运维。仓库现在有四个服务：`antigravity`、`workbuddy`、
`aistudio` 和 `sub2api`。Sub2API 默认发布宿主端口 `8080`。

本文是部署和验收手册。2026-11-25 的 Playground 迁移实测记录见
[AI Studio Playground 迁移验证](validation/aistudio-playground-migration-20261125.md)；
该记录中的 7870 是当时 LXC 宿主侧入口。除链接记录明确写出的实测结果外，本文的
“预期”内容是验收标准，不表示本次文档更新重新运行过。

## 1. 服务契约

| 项目 | 当前值 |
| --- | --- |
| Compose 服务名 | `aistudio` |
| 镜像 | `ghcr.io/mag1cfall/aistudio2api:latest` |
| 容器监听 | `LISTEN_ADDR=0.0.0.0:2048` |
| 默认宿主端口 | `2048`，由 `AISTUDIO_PORT` 覆盖 |
| LXC158 宿主端口 | `7870`，映射到容器 `2048` |
| Compose 内 URL | `http://aistudio:2048` |
| LXC 本机 URL | `http://127.0.0.1:7870` |
| 管理鉴权 | `ADMIN_AUTH_ENABLED=true`，用户名 `ADMIN_USERNAME` |
| 生成鉴权 | `PROXY_API_KEY`，来自 `AISTUDIO_API_KEY` |
| 生成通道 | `UPSTREAM_CHANNELS=playground` |
| 数据目录 | `/opt/ai-proxy-kit-aistudio/runtime/aistudio/data` -> `/data` |

`compose.yml` 固定容器端口 `2048`。只改 `AISTUDIO_PORT` 不会改变容器内监听地址；
因此 LXC 上看到 `7870` 时，应先确认它只是 `ports: ["0.0.0.0:${AISTUDIO_PORT:-2048}:2048"]`
的左侧宿主端口。

## 2. 配置映射

| `runtime/stack.env` | 容器变量 | 说明 |
| --- | --- | --- |
| `AISTUDIO_API_KEY` | `PROXY_API_KEY` | `/v1` Bearer key，也是 Sub2API 上游 key |
| `AISTUDIO_ADMIN_USER` | `ADMIN_USERNAME` | 管理登录用户名，缺省为 `admin` |
| `AISTUDIO_ADMIN_PASSWORD` | `ADMIN_PASSWORD` | 管理登录密码，必填 |
| `AISTUDIO_CHANNELS` | `UPSTREAM_CHANNELS` | 使用 `playground` |
| `AISTUDIO_ADMIN_AUTH` | `ADMIN_AUTH_ENABLED` | 默认 `true`，保护 `/api` |
| `AISTUDIO_PORT` | 宿主端口 | LXC158 设为 `7870`；容器仍是 `2048` |

凭据保存在权限 `600` 的 `runtime/stack.env`。不要 `cat`、`grep` 出值后贴到终端输出、日志、
文档或提交中。旧 Build 的 `AISTUDIO_WEB_PASSWORD` 不再是运行配置；安装流程会把它迁移为
`AISTUDIO_ADMIN_PASSWORD`。

## 3. 检查 LXC 和部署目录

在 PVE 主机执行：

```bash
pct status 158
```

预期为 `status: running`。若未运行，先执行 `pct start 158` 并等待系统启动。

进入 LXC shell：

```bash
pct exec 158 -- bash
```

以下命令在 LXC 内执行。先确认部署目录和配置文件存在，不打印配置值：

```bash
cd /opt/ai-proxy-kit-aistudio
test -f compose.yml
test -s runtime/stack.env
stat -c '%a %n' runtime/stack.env
for key in AISTUDIO_API_KEY AISTUDIO_PORT SUB2API_PASSWORD; do
  grep -q "^${key}=" runtime/stack.env || echo "MISSING=${key}"
done
grep -q '^AISTUDIO_WEB_PASSWORD=' runtime/stack.env && echo LEGACY_BUILD_ENV=present
```

`runtime/stack.env` 必须是 `600`。出现 `MISSING=` 时不要手工回显文件内容；先完成下一节
安装迁移，或从受控秘密管理恢复。`LEGACY_BUILD_ENV=present` 只表示旧变量仍在，不能说明
当前 Playground 配置已经就绪。

## 4. 安装、迁移与启动

首次把 LXC 的 AI Studio 迁移到仓库 Compose 服务时，显式保留宿主端口 `7870`：

```bash
bash install.sh aistudio --aistudio-port 7870
```

该入口会读取或创建 `runtime/stack.env`，将旧 Build 密码变量迁移为
`AISTUDIO_ADMIN_PASSWORD`，将旧的 `AISTUDIO_PORT=7860` 改为 `2048`，再应用
`--aistudio-port 7870`；随后准备镜像、启动 `aistudio` 并等待根页面可达。

如果同时维护四个服务，首次安装可以执行：

```bash
bash install.sh all --aistudio-port 7870
```

日常启动不需要重复安装：

```bash
bash start.sh aistudio
# 或启动四个服务
bash start.sh all
```

安装或启动完成后，`runtime/stack.env` 中应存在 `AISTUDIO_ADMIN_PASSWORD`，且
`AISTUDIO_PORT` 为 `7870`。仅运行 `start.sh` 不会执行旧变量迁移。

## 5. Compose 配置与端口验收

先校验 Compose 配置：

```bash
docker compose --env-file runtime/stack.env --profile aistudio config --quiet
echo COMPOSE_CONFIG_OK
```

预期出现 `COMPOSE_CONFIG_OK`。变量缺失时不要用 `set -x` 或打印 env 内容排障。

检查服务和宿主端口：

```bash
docker compose --env-file runtime/stack.env --profile aistudio ps aistudio
docker compose --env-file runtime/stack.env --profile aistudio port aistudio 2048
curl -sS -o /dev/null -w 'root_http=%{http_code}\n' http://127.0.0.1:7870/
```

合格条件是服务为 running、端口发布到 `0.0.0.0:7870`，根页面返回非 5xx。AIStudio2API
没有 `/health` 接口；根页面 200 只证明 Web 管理进程可达，不能证明账号可生成。

检查容器内监听和通道，不输出秘密：

```bash
docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio printenv LISTEN_ADDR UPSTREAM_CHANNELS
```

预期：

```text
0.0.0.0:2048
playground
```

如果容器变量是 `0.0.0.0:7870`，说明改动偏离了当前 Compose 契约。宿主端口 `7870` 应与
容器 `2048` 分开检查。

## 6. 凭据映射验收

以下命令只输出 `yes`/`no`，不打印 key 或密码：

```bash
cd /opt/ai-proxy-kit-aistudio
env_key=$(sed -n 's/^AISTUDIO_API_KEY=//p' runtime/stack.env | tr -d "'")
container_key=$(docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio printenv PROXY_API_KEY)
test -n "$env_key" && test "$env_key" = "$container_key" && echo KEY_MATCH=yes || echo KEY_MATCH=no

env_pass=$(sed -n 's/^AISTUDIO_ADMIN_PASSWORD=//p' runtime/stack.env | tr -d "'")
container_pass=$(docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio printenv ADMIN_PASSWORD)
test -n "$env_pass" && test "$env_pass" = "$container_pass" \
  && echo ADMIN_PASSWORD_MATCH=yes || echo ADMIN_PASSWORD_MATCH=no

env_user=$(sed -n 's/^AISTUDIO_ADMIN_USER=//p' runtime/stack.env | tr -d "'")
env_user=${env_user:-admin}
container_user=$(docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio printenv ADMIN_USERNAME)
test "$env_user" = "$container_user" && echo ADMIN_USER_MATCH=yes || echo ADMIN_USER_MATCH=no
```

三项应为 `yes`。`KEY_MATCH=no` 会导致客户端的 `/v1` 请求返回 401；密码或用户不匹配会
导致管理登录失败。确认 `runtime/stack.env` 后重建容器，再重新执行本节。

## 7. `/v1` 鉴权与模型列表

匿名请求必须被拒绝，带 key 请求必须返回非空列表：

```bash
cd /opt/ai-proxy-kit-aistudio
env_key=$(sed -n 's/^AISTUDIO_API_KEY=//p' runtime/stack.env | tr -d "'")
anon=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:7870/v1/models)
test "$anon" = 401 && echo ANON_STATUS=401 || echo "UNEXPECTED_ANON_STATUS=$anon"
curl -fsS -H "Authorization: Bearer $env_key" http://127.0.0.1:7870/v1/models |
  python3 -c 'import json,sys; d=json.load(sys.stdin); ms=d.get("data") or d.get("models") or []; print("MODEL_COUNT=%d" % len(ms)); [print("MODEL=" + m["id"]) for m in ms]'
```

`MODEL_COUNT` 必须是正整数。模型 ID 以本次返回为准，不要沿用历史记录。更完整的自动检查
使用：

```bash
bash check.sh aistudio
```

预期：

```text
aistudio: auth PASS; <N> model(s).
```

## 8. 导入 Google 会话

没有有效 Google 会话时，不要继续做真实生成。先把导出的 Playwright storage-state JSON
放到 LXC 的受控路径，例如 `/root/ai-proxy-kit-google-session.json`，然后在 LXC 内执行：

```bash
cd /opt/ai-proxy-kit-aistudio
install -m 600 /root/ai-proxy-kit-google-session.json runtime/aistudio/data/session.json
docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio aistudio2api setup --storage-state /data/session.json
rm -f runtime/aistudio/data/session.json
```

`runtime/aistudio/data` 是 Compose 的 `/data` 挂载；导入前确认目录已由安装流程创建。
命令不输出会话内容。`setup` 成功只表示文件被管理进程接收，账号是否可用仍要查看
`/api/status` 并完成生成验收。

Windows 本机的等价入口是：

```powershell
.\import-aistudio2api-session.cmd -DataDir .\runtime\aistudio\data
```

导出文件、Cookie、邮箱和账号信息都不得提交到仓库。

## 9. 账号就绪与真实生成

使用当前 `/v1/models` 中的第一个模型做完整检查：

```bash
cd /opt/ai-proxy-kit-aistudio
env_key=$(sed -n 's/^AISTUDIO_API_KEY=//p' runtime/stack.env | tr -d "'")
model=$(curl -fsS -H "Authorization: Bearer $env_key" http://127.0.0.1:7870/v1/models |
  python3 -c 'import json,sys; d=json.load(sys.stdin); ms=d.get("data") or d.get("models") or []; print(ms[0]["id"] if ms else "")')
test -n "$model" || { echo 'NO_MODEL_LISTED'; exit 1; }
printf 'ACCEPTANCE_MODEL=%s\n' "$model"
bash check.sh aistudio --model "$model"
```

`check.sh --model` 会完成匿名 401、鉴权模型列表、管理登录、`/api/status` 账号检查和一次
非流式生成，不打印密码。合格输出形状：

```text
ACCEPTANCE_MODEL=<当前实际模型 ID>
aistudio: auth PASS; <N> model(s).
AI Studio console: <ready>/<total> account(s) ready (RUNNING).
aistudio: real non-streaming generation PASS (PROXY_KIT_OK)
```

账号就绪至少要求：

- `state=RUNNING`；
- `running=true`；
- 空闲验收时 `accounts.ready>=1`。

容器 running、根页面 200、模型列表非空、`accounts.total>=1` 都不能单独代替以上判据。
只有最后一行出现 `PROXY_KIT_OK`，才能记录为本次生成通过。

## 10. Sub2API 接入

同属仓库 Compose 栈时，`connect.sh` 使用服务名 `aistudio` 和容器端口 `2048`：

```bash
cd /opt/ai-proxy-kit-aistudio
bash connect.sh --provider aistudio
bash connect.sh --provider aistudio --test --model "$model"
```

预期上游为：

```text
http://aistudio:2048
```

只有经 Sub2API 的 provider key 做真实生成并返回 `PROXY_KIT_OK`，才能把路由记为通过。
仅能从 `sub2api` 容器访问 2048，不等于账号路由已经可用。

如果 Sub2API 不在同一 Compose 网络，才使用 LXC 的宿主机发布端口。按当前环境核对宿主机
容器网关地址，例如：

```bash
bash connect.sh --provider aistudio --test \
  --upstream-url http://172.18.0.1:7870
```

`172.18.0.1:7870` 是跨栈路径；同一 Compose 栈应使用 `http://aistudio:2048`。不要填写
Sub2API 容器自己的 `127.0.0.1`，也不要继续使用旧 Build 的 7860 路由。

## 11. 验收记录

只记录非秘密字段：

| 检查项 | 合格标准 |
| --- | --- |
| LXC | `pct status 158` 为 running |
| Compose 服务 | `aistudio` 为 running |
| 容器监听 | `0.0.0.0:2048` |
| LXC 宿主映射 | `0.0.0.0:7870 -> 2048` |
| 通道 | `UPSTREAM_CHANNELS=playground` |
| key 映射 | `KEY_MATCH=yes` |
| 管理凭据 | `ADMIN_USER_MATCH=yes`、`ADMIN_PASSWORD_MATCH=yes` |
| `/v1` 匿名请求 | HTTP 401 |
| `/v1/models` | 带 key 返回正整数个模型 |
| `/api/status` | `RUNNING`，`accounts.ready>=1` |
| 直连生成 | 当前模型返回 `PROXY_KIT_OK` |
| Sub2API 同栈上游 | `http://aistudio:2048` |
| Sub2API 路由生成 | 经 provider key 返回 `PROXY_KIT_OK` |

记录中只写日期、容器/服务、模型 ID、HTTP 状态、账号计数和 PASS/FAIL，不写 key、密码、
Cookie、token、认证文件内容或完整日志。

## 12. 常见失败定位

按“容器和服务名 -> 宿主端口 -> 凭据映射 -> `/v1` -> 账号状态 -> Sub2API 路由”的顺序
排查。

| 现象 | 判断与处理 |
| --- | --- |
| `compose config` 报变量缺失 | 先运行 `bash install.sh aistudio --aistudio-port 7870`；不要打印 env 内容 |
| 宿主 7870 不可达 | 检查服务是否 running、`ports` 是否为 `AISTUDIO_PORT=7870`、端口是否冲突 |
| 容器内看到 7870 | 当前 Compose 应监听 2048；检查是否误用了旧独立部署配置 |
| `/v1/models` 匿名不是 401 | 检查实际访问的是否为本手册的 `aistudio` 服务和当前端口 |
| `/v1/models` 带 key 401 | 执行第 6 节；key 映射不一致时重建容器 |
| 管理登录失败 | 核对 `AISTUDIO_ADMIN_USER` / `AISTUDIO_ADMIN_PASSWORD`，再检查 `/api/status` |
| `/api/status` 非 `RUNNING` | 在 Playground 管理端启动生成服务，或查看日志和 `AUTO_START` |
| `accounts.ready=0` | 导入有效 storage-state，必要时重新完成本人 Google 登录 |
| 生成 503 或无可用账号 | 账号/会话问题；先看状态，不要反复重启容器 |
| 同栈 Sub2API 连不上 | 上游应为 `http://aistudio:2048`，不是 `127.0.0.1` 或 7870 |
| 跨栈 Sub2API 连不上 | 核对宿主机容器网关和 `7870` 映射，再检查防火墙 |

## 13. 历史记录

7860 Build、noVNC 和旧登录诊断记录仅用于解释历史错误形状，不能作为当前操作指引：

- [Windows 本机 AI Studio 反代测试（历史）](validation/windows-aistudio-proxy-20261124.md)
- [AI Studio noVNC 本地化验证（历史）](validation/aistudio-novnc-local-20261125.md)
- [AI Studio 登录诊断（历史）](validation/aistudio-login-20261125.md)

当前 Playground 迁移、7870 宿主端口和生成结果见
[AI Studio Playground 迁移验证](validation/aistudio-playground-migration-20261125.md)。
Build 通道已停用，旧 7860 反代不再作为生成上游。