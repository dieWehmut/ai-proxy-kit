# LXC 158 AI Studio 部署与验收手册

> **Build 通道已停用，请使用 Playground(AIStudio2API) 作为上游。**
>
> 2026-11-25 的验证确认：在 VNC 登录完成、`auth-1` 未过期且已点过
> **Continue to the app** 的情况下，7860 的 `ai-proxy-kit-aistudio-1`
> （`ibuhub/aistudio-to-api:v1.4.1`）仍以 HTTP 400
> `PROXY_GENERATION_DISABLED: generateContent` 拒绝非流式生成，流式请求对应
> `streamGenerateContent`。上游
> [iBUHub/AIStudioToAPI issue #302](https://github.com/iBUHub/AIStudioToAPI/issues/302)
> 已公告 Google 于 2026-10-30 关闭 Build 渠道，因此这不是账号、网络或本地配置问题。
> 7860 仅保留 UI/管理用途。当前生成上游是 `ghcr.io/mag1cfall/aistudio2api` 的
> Playground 通道，监听 `0.0.0.0:7870`；Sub2API 应指向
> `http://172.18.0.1:7870`。完整实测记录见
> [aistudio-playground-migration-20261125.md](validation/aistudio-playground-migration-20261125.md)。

本文面向在 PVE 节点上通过 `pct exec 158` 运维 AI Studio、Playground 和 Sub2API 的场景。
部署目录固定为 `/opt/ai-proxy-kit-aistudio`。7860 的 Build 反代仅用于 UI/管理；
7870 的 Playground 是当前生成上游；Sub2API 对外端口为 `8080`。

本文是部署与验收标准。除特别标注为 2026-11-25 已验证的 7870 结果外，下面标注为“预期”
的内容是合格标准和输出形状；历史观察单独放在 [历史证据](#历史证据) 一节，不能把历史
模型 ID 或历史结果当作当前环境的保证。下文第 1-12 节保留 7860 Build 通道的部署、控制和
排障资料；其中涉及的旧生成验收不得作为当前结论，当前生成验收一律按下一节执行。

## 当前生成通道：Playground(AIStudio2API) on 7870

| 项目 | 当前值 |
| --- | --- |
| 生成上游镜像 | `ghcr.io/mag1cfall/aistudio2api` |
| 监听地址 | `LISTEN_ADDR=0.0.0.0:7870` |
| 管理接口 `/api` | `ADMIN_AUTH_ENABLED=true`，必须通过管理员认证 |
| 生成接口 `/v1` | 由 `PROXY_API_KEY` 保护 |
| 会话导入 | `aistudio2api setup --storage-state` |
| 已验证状态 | `/api/status` 为 `RUNNING`，`accounts.ready=1` |
| 已验证模型 | `gemini-4-hydrogen`、`gemini-3.8-flash`（非流式均返回 `PROXY_KIT_OK`） |
| Sub2API 上游地址 | `http://172.18.0.1:7870` |

### 7870 配置

在 LXC158 的 aistudio2api 服务环境中配置以下变量。`PROXY_API_KEY` 必须从秘密管理或
受控环境注入，不能写进仓库、终端回显或本手册：

```dotenv
LISTEN_ADDR=0.0.0.0:7870
ADMIN_AUTH_ENABLED=true
PROXY_API_KEY=<由秘密管理注入>
```

`LISTEN_ADDR` 不能再停留在旧的 `127.0.0.1:7870`。修改后重启或重建 aistudio2api 服务，
让新监听地址和鉴权配置生效。`ADMIN_AUTH_ENABLED=true` 保护 `/api`；`/v1` 的每个请求都必须
携带与 `PROXY_API_KEY` 一致的 Bearer token。

使用与 7860 测试相同的会话执行导入，按 CLI 提示提供受控文件路径。不要把会话文件路径、
内容、邮箱或 Cookie 写入仓库或日志：

```bash
aistudio2api setup --storage-state
```

### 7870 验收

先确认实际监听地址：

```bash
ss -lntp | grep ':7870'
```

预期出现 `0.0.0.0:7870`。若只有 `127.0.0.1:7870`，Sub2API 容器无法通过宿主机地址接入。

通过已认证的管理端或受控的本机管理通道读取 `/api/status`，不要匿名请求 `/api`，也不要
把管理员凭据放进命令历史：

```bash
curl -fsS -H "<管理员认证头>" http://127.0.0.1:7870/api/status
```

预期状态包含：

```text
status: RUNNING
accounts.ready: 1
```

再从 `sub2api` 容器验证到宿主机 7870 的网络和 `/v1` 鉴权。`PROXY_API_KEY` 由环境注入，
命令只引用变量：

```bash
docker exec -e PROXY_API_KEY="$PROXY_API_KEY" <sub2api-container> sh -lc \
  'curl -sS -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Bearer $PROXY_API_KEY" \
    http://172.18.0.1:7870/v1/models'
```

预期 HTTP 状态为 `200`。随后使用当前模型列表中的 ID 做非流式生成：

```bash
for model in gemini-4-hydrogen gemini-3.8-flash; do
  curl -sS \
    -H "Authorization: Bearer $PROXY_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply OK\"}],\"stream\":false}" \
    http://172.18.0.1:7870/v1/chat/completions
  echo
done
```

两个模型均应返回 `PROXY_KIT_OK`。匿名 `/api` 请求不能返回管理数据；未带 `PROXY_API_KEY`
的 `/v1` 请求不能进入生成逻辑。

### Sub2API 指向 7870

在 Sub2API 管理端把 AI Studio 上游账号改为：

| 字段 | 当前值 |
| --- | --- |
| Base URL | `http://172.18.0.1:7870` |
| API Key | 与 aistudio2api 的 `PROXY_API_KEY` 一致 |

不要继续使用 `http://aistudio:7860`。从 `sub2api` 容器直连 7870 成功只能证明网络和
接口可达；修改账号后还必须选择当前 `/v1/models` 中的模型，经 Sub2API 做一次非流式生成，
只有返回 `PROXY_KIT_OK` 才能把路由验收记为通过。

## 7860 Build 通道部署契约（仅 UI/管理）

| 项目 | 验收值 | 依据 |
| --- | --- | --- |
| PVE 入口 | 在 PVE 主机执行 `pct exec 158 -- ...` | 本手册的目标拓扑 |
| 部署目录 | `/opt/ai-proxy-kit-aistudio` | 本手册的目标部署路径 |
| 容器工作目录 | `/opt/ai-proxy-kit-aistudio` | 目标部署约束；noVNC 预加载使用进程当前目录查找 `ui/dist`，见 `services/aistudio-novnc/preload.cjs:6-9` |
| 7860 Build UI/管理 | 宿主 `0.0.0.0:7860` -> 容器 `7860`；生成已停用 | `compose.yml:115-120` |
| 7870 Playground | `0.0.0.0:7870`，当前生成上游 | aistudio2api 服务配置 |
| 7860 Compose 网络别名 | 仅 `ibuhub/aistudio-to-api` 旧栈使用 `aistudio:7860` | `scripts/connect-sub2api.py:126-127` |
| Sub2API 端口 | 宿主 `0.0.0.0:8080` -> 容器 `8080` | `compose.yml:49-58` |
| 7860 Build 镜像 | `ibuhub/aistudio-to-api:v1.4.1`（仅 UI/管理） | `compose.yml:114` |
| 7870 Playground 镜像 | `ghcr.io/mag1cfall/aistudio2api` | 2026-11-25 迁移记录 |
| 配置与凭据 | `runtime/stack.env`，权限 `600`；不得打印或提交 | `lib/proxykit.py:58-74` |
| Google 会话 | 生成前必须有有效会话；`browserConnected=true` 且当前账号未过期/未失效 | `lib/proxykit.py:340-369`、`scripts/import-aistudio-session.py:110-144` |
| Sub2API 上游账号 | `platform=openai`、`type=apikey`，`openai_responses_mode=force_chat_completions`，`openai_responses_supported=false` | `scripts/connect-sub2api.py:143-148` |
| Sub2API 上游地址 | `http://172.18.0.1:7870` | 2026-11-25 迁移记录 |
| 模型 | 客户端和 Sub2API 测试必须使用当前 Playground `/v1/models` 实际返回的 ID | `lib/proxykit.py:356-360`、`scripts/connect-sub2api.py:200-212` |

7860 的 Build UI、7870 的 Playground 生成接口和 Sub2API 的 8080 是三层服务。
`GET :7860/` 成功只证明旧 Build 反代的 UI/管理可用；Build 的 `/v1/models` 成功也不能
证明生成可用。当前生成验收必须对 7870 执行，Sub2API 则必须经 7870 做路由验收。

## 操作约束

- 只读取状态、计数、容器 ID、HTTP 状态码，不输出 `runtime/stack.env`、Cookie、OAuth Token、
  API Key、控制台密码或完整认证文件。
- 不执行 `git pull`、镜像升级或未经验证的数据库写入。需要先确认本地镜像和当前源码版本。
- 改动 `runtime/stack.env` 后必须重建对应容器；只改文件不会让运行中的容器自动加载新值。
- 必须先建立有效 Google 会话，再进行真实生成和 Sub2API 路由验收。
- 容器 healthy、端口可连、模型列表非空，都不等于生成通过。最终以指定模型返回
  `PROXY_KIT_OK` 为通过证据。
- 容器名会随 Compose project 配置变化。历史记录中出现过
  `ai-proxy-kit-aistudio-aistudio-1`，但当前 `compose.yml` 声明了项目名 `ai-proxy-kit`。
  因此下面的命令通过 Compose label 和工作目录发现容器，不写死容器名。

## 1. 确认 LXC 状态

在 PVE 主机执行：

```bash
pct status 158
```

预期：

```text
status: running
```

若不是 `running`，先启动容器并等待其完成启动：

```bash
pct start 158
pct status 158
```

## 2. 检查部署目录和配置文件

只检查文件是否存在、权限是否为 `600`，不打印内容：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
pwd
test -f compose.yml
test -f compose.aistudio-novnc.yml
test -s runtime/stack.env
stat -c "%a %n" runtime/stack.env
for key in AISTUDIO_API_KEY AISTUDIO_WEB_PASSWORD SUB2API_EMAIL SUB2API_PASSWORD; do
  grep -q "^$key=" runtime/stack.env || { echo "MISSING=$key"; exit 1; }
done
echo PREFLIGHT_OK
'
```

预期：

```text
/opt/ai-proxy-kit-aistudio
600 /opt/ai-proxy-kit-aistudio/runtime/stack.env
PREFLIGHT_OK
```

如果权限不是 `600`，在确认目录归属后修正：

```bash
pct exec 158 -- chmod 600 /opt/ai-proxy-kit-aistudio/runtime/stack.env
```

若出现 `MISSING=...`，不要手工回显配置文件。先确认部署是否完整；缺项应由安装流程补充，
或由现有秘密管理系统恢复。

## 3. 检查 Docker 和 Compose 配置

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
docker info --format "docker_server={{.ServerVersion}}"
docker compose \
  --env-file runtime/stack.env \
  -f compose.yml \
  -f compose.aistudio-novnc.yml \
  config --quiet
echo COMPOSE_CONFIG_OK
'
```

预期：

```text
docker_server=<版本号>
COMPOSE_CONFIG_OK
```

`config --quiet` 成功时通常没有其他输出。若出现变量缺失，先定位 `runtime/stack.env` 的键，
不要通过 `cat` 或 `set -x` 打印秘密。

## 4. 安装或启动 AI Studio 和 Sub2API

首次部署使用安装入口。`install.sh all` 不包含 AI Studio，AI Studio 需要单独指定：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
bash install.sh aistudio
bash install.sh sub2api
'
```

`install.sh` 会复用已有配置和密钥；镜像已存在时输出 `Using cached image`，缺失时才尝试拉取。
如果环境要求完全离线，先确认以下镜像都已存在，再改用 `bash start.sh aistudio --skip-pull`
和 `bash start.sh sub2api --skip-pull`：

```text
ibuhub/aistudio-to-api:v1.4.1
weishaw/sub2api:0.2.23
postgres:18-alpine
redis:8-alpine
```

AI Studio 的合格输出形状：

```text
Using cached image: ibuhub/aistudio-to-api:v1.4.1
aistudio: listening on 0.0.0.0:7860; local URL http://127.0.0.1:7860
Credentials stay in /opt/ai-proxy-kit-aistudio/runtime/stack.env; no secrets are printed.
```

Sub2API 的合格输出形状：

```text
sub2api: listening on 0.0.0.0:8080; local URL http://127.0.0.1:8080
```

镜像缺失、端口冲突或 Docker daemon 不可达时，不应继续做生成验收。

## 5. 验收容器、端口和工作目录

下面的命令先按 Compose label 找到 AI Studio 容器，再检查容器状态、镜像、端口和工作目录：

```bash
pct exec 158 -- bash -lc '
set -e
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
test -n "$name"
docker inspect --format \
  "name={{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} image={{.Config.Image}} workdir={{.Config.WorkingDir}}" \
  "$name"
docker ps --filter "id=$name" --format "ports={{.Ports}}"
docker exec "$name" pwd
docker exec "$name" printenv HOST PORT
'
```

预期形状：

```text
name=/<compose-project>-aistudio-1 status=running health=healthy image=ibuhub/aistudio-to-api:v1.4.1 workdir=/opt/ai-proxy-kit-aistudio
ports=0.0.0.0:7860->7860/tcp
/opt/ai-proxy-kit-aistudio
0.0.0.0
7860
```

容器名和 Compose project 前缀可以不同；必须满足的是 label 所属工作目录、`health=healthy`、
端口映射和 `/opt/ai-proxy-kit-aistudio`。如果 `workdir` 或 `docker exec pwd` 不是目标目录，
noVNC 预加载可能找不到 `ui/dist`，应先修复部署，不要继续生成测试。

检查 AI Studio 根页面、健康接口和 Sub2API 健康接口：

```bash
pct exec 158 -- bash -lc '
curl -sS -o /dev/null -w "aistudio_root_http=%{http_code}\n" http://127.0.0.1:7860/
curl -fsS http://127.0.0.1:7860/health
echo
curl -fsS http://127.0.0.1:8080/health
echo
'
```

预期：

```text
aistudio_root_http=200
{"status":"ok","browserConnected":true,...}
{"status":"ok"}
```

`browserConnected` 此处用于快速判断；完整的账号有效性还要看下一节的 `/api/status` 检查。

## 6. 验收 7860 Build Compose 网络别名（历史）

本节的别名检查只针对 7860 Build UI/管理旧栈，用来保留旧容器的网络排障方法：

```bash
pct exec 158 -- bash -lc '
set -e
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
sub=$(docker ps -q \
  --filter label=com.docker.compose.service=sub2api \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
test -n "$name"
test -n "$sub"
printf "aistudio_container=%s\n" "$name"
docker inspect --format \
  "{{range \$net,\$cfg := .NetworkSettings.Networks}}{{\$net}} alias={{range \$cfg.Aliases}}{{.}} {{end}}{{println}}{{end}}" \
  "$name"
project=$(docker inspect --format "{{index .Config.Labels \"com.docker.compose.project\"}}" "$name")
network=$(docker network ls \
  --filter label=com.docker.compose.project="$project" \
  --format "{{.Name}}" | head -n 1)
printf "network=%s\n" "$network"
docker network inspect "$network" --format "{{range .Containers}}{{.Name}}{{println}}{{end}}"
'
```

预期形状：

```text
aistudio_container=<container-id>
<network-name> alias=<aistudio-container> aistudio
network=<network-name>
<aistudio-container>
<sub2api-container>
```

别名列表中必须出现 `aistudio`，容器列表中必须同时出现 AI Studio 和 Sub2API。该别名
不再是当前生成上游；Sub2API 生成账号必须使用 `http://172.18.0.1:7870`，不要使用
`http://aistudio:7860` 或 Sub2API 容器内的 `127.0.0.1`。

## 7. 验收 API Key、控制台密码和模型列表

该步骤只输出匹配结果、HTTP 状态和模型 ID，不输出秘密：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
test -n "$name"
env_key=$(grep ^AISTUDIO_API_KEY= runtime/stack.env | cut -d= -f2- | tr -d "\047")
env_pass=$(grep ^AISTUDIO_WEB_PASSWORD= runtime/stack.env | cut -d= -f2- | tr -d "\047")
container_keys=$(docker exec "$name" printenv API_KEYS)
container_pass=$(docker exec "$name" printenv WEB_CONSOLE_PASSWORD)
if printf "%s\n" "$container_keys" | tr , "\n" | grep -Fxq "$env_key"; then
  echo KEY_MATCH=yes
else
  echo KEY_MATCH=no
  exit 1
fi
if test "$env_pass" = "$container_pass"; then
  echo CONSOLE_PASSWORD_MATCH=yes
else
  echo CONSOLE_PASSWORD_MATCH=no
  exit 1
fi
anon=$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:7860/v1/models)
test "$anon" = 401
echo ANON_STATUS=401
curl -fsS -H "Authorization: Bearer $env_key" http://127.0.0.1:7860/v1/models |
python3 -c "import json,sys; d=json.load(sys.stdin); ms=d.get(\"data\",[]); print(\"MODEL_COUNT=%d\" % len(ms)); [print(\"MODEL=\" + m[\"id\"]) for m in ms]"
'
```

预期：

```text
KEY_MATCH=yes
CONSOLE_PASSWORD_MATCH=yes
ANON_STATUS=401
MODEL_COUNT=<正整数>
MODEL=<当前实际模型 ID>
```

`KEY_MATCH=no` 时，客户端可能拿到 401，Sub2API 也会拿错误的 key。历史记录曾出现
`stack.env` 与运行容器 `API_KEYS` 不一致；这不是 Google 会话问题。确认配置来源后重建容器，
再重新执行本节。`CONSOLE_PASSWORD_MATCH=no` 时，控制台登录和会话导入都会失败。

模型数量不是固定值；历史记录中 Windows 环境曾看到 31 个模型，LXC158 的另一时点曾只看到
`gemini-4-hydrogen`。验收以本次 `/v1/models` 返回为准，不能抄历史值。

## 8. 先建立有效 Google 会话

没有有效 Google 会话时，不要执行真实生成和 Sub2API `--test`。有两种建立方式。

### 8.1 导入本机导出的 Google 会话

先把导出文件以安全方式放到 LXC 的受控路径，例如 `/root/ai-proxy-kit-google-session.json`，
再执行：

```bash
pct exec 158 -- bash -lc '
cd /opt/ai-proxy-kit-aistudio
python3 scripts/import-aistudio-session.py /root/ai-proxy-kit-google-session.json
'
```

预期形状：

```text
Imported <N> Google cookies in auth-<N>.json (mode 600).
AI Studio {'browserConnected': True, 'currentAuthIndex': <index>, 'isSystemBusy': False, 'expiredAccounts': 0, 'invalidAccounts': 0, 'importedAccount': 'recognized', 'ready': True}
Verify real generation through Sub2API next.
```

重复导入同一份会话时，第一行可以是 `Reused ...`。只有 `ready=True` 才满足生成前置条件。
导入文件含登录会话，不要提交、不要贴到聊天或日志中。

### 8.2 在 AI Studio 控制台的 VNC/Google 页面登录

从受控客户端打开 `http://<LXC158_IP>:7860/`，使用 `runtime/LOCAL_ACCESS.txt` 中的控制台密码
进入 VNC/Google 页面并完成本人登录。不要用 `cat` 回显该文件。

登录后重新读取控制台状态。该命令复用仓库中的控制台登录和状态解析逻辑，只打印摘要：

```bash
pct exec 158 -- bash -lc '
cd /opt/ai-proxy-kit-aistudio
python3 -c "import sys,pathlib,importlib.util; sys.path.insert(0,\"lib\"); import proxykit; s=importlib.util.spec_from_file_location(\"session\",\"scripts/import-aistudio-session.py\"); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.reload_auth(proxykit.read_env(pathlib.Path(\"runtime/stack.env\"))))"
'
```

预期形状：

```text
{'browserConnected': True, 'currentAuthIndex': 0, 'isSystemBusy': False, 'expiredAccounts': 0, 'invalidAccounts': 0, 'importedAccount': 'unchecked', 'ready': True}
```

合格条件：

- `browserConnected=True`
- `isSystemBusy=False`
- `currentAuthIndex` 是有效账号索引，不能是 `-1`
- `expiredAccounts=0` 且 `invalidAccounts=0`
- `ready=True`

登录成功后，检查认证挂载和文件权限，不读取文件内容：

```bash
pct exec 158 -- bash -lc '
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
docker inspect --format \
  "{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}} rw={{.RW}}{{println}}{{end}}" \
  "$name"
docker exec "$name" sh -c "ls -l /app/configs/auth/auth-*.json"
'
```

预期包含 `/opt/ai-proxy-kit-aistudio/runtime/aistudio/auth -> /app/configs/auth` 的 bind mount，
以及权限为 `600` 的 `/app/configs/auth/auth-N.json`。

## 9. 7860 Build 直连验收（历史，生成已停用）

> 本节只保留 7860 Build 通道的历史验收方法。2026-11-25 起 Build 生成已被上游关闭，
> 不要再把本节结果作为当前生成是否可用的依据；当前验收使用上面的 7870 Playground 步骤。

先查询当前模型，再用同一个模型 ID 执行完整直连检查。`check.sh` 会验证匿名 401、鉴权模型列表、
模型是否属于当前列表、Google 会话标志和真实生成；它不会打印 key：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
key=$(docker exec "$name" printenv API_KEYS | cut -d, -f1)
model=$(curl -fsS -H "Authorization: Bearer $key" http://127.0.0.1:7860/v1/models |
  python3 -c "import json,sys; print(json.load(sys.stdin)[\"data\"][0][\"id\"])")
printf "ACCEPTANCE_MODEL=%s\n" "$model"
bash check.sh aistudio --model "$model"
'
```

预期：

```text
ACCEPTANCE_MODEL=<当前实际模型 ID>
aistudio: auth PASS; <N> model(s).
AI Studio browserConnected=True; complete Google login in its Web/VNC UI.
aistudio: real non-streaming generation PASS (PROXY_KIT_OK)
```

`browserConnected=True` 那行仍会打印，这是状态信息；合格的关键是最后一行出现
`real non-streaming generation PASS (PROXY_KIT_OK)`。如果只需要非流式验收，到这里直连层已通过。

可选流式验收使用同一个模型：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
key=$(docker exec "$name" printenv API_KEYS | cut -d, -f1)
model=$(curl -fsS -H "Authorization: Bearer $key" http://127.0.0.1:7860/v1/models |
  python3 -c "import json,sys; print(json.load(sys.stdin)[\"data\"][0][\"id\"])")
bash check.sh aistudio --model "$model" --stream
'
```

预期最后一行：

```text
aistudio: real streaming generation PASS (PROXY_KIT_OK)
```

## 10. 配置 Sub2API 上游账号（历史 Build 配置）

> 本节中的 `http://aistudio:7860` 只属于已停用的 Build 通道。当前 Sub2API 上游必须使用
> 上面的 `http://172.18.0.1:7870`，不要照抄本节地址。

先在 `http://<LXC158_IP>:8080/` 完成首次管理员确认和登录。账号配置由脚本创建或复用：

```bash
pct exec 158 -- bash -lc '
cd /opt/ai-proxy-kit-aistudio
bash connect.sh --provider aistudio
'
```

预期形状：

```text
Sub2API: AI Proxy Kit / Aistudio: account=<id> group=<id> upstream=http://aistudio:7860
```

在 Sub2API 管理页面打开 `AI Proxy Kit / Aistudio` 账号，核对以下字段：

| 字段 | 要求 |
| --- | --- |
| 平台 | `openai` |
| 类型 | `apikey` |
| Base URL | `http://aistudio:7860` |
| 分组 | `AI Proxy Kit / Aistudio` |
| Responses 模式 | `force_chat_completions` |
| Responses 支持 | `false` |

源码只在新建账号时写入
`{"openai_responses_mode":"force_chat_completions","openai_responses_supported":false}`
（`scripts/connect-sub2api.py:143-148`）。如果账号是旧版本创建的，重复运行脚本只校验
平台、类型、Base URL 和 key，不会自动补齐 `extra`。因此必须读回页面字段；不正确时在管理页
修正并保存，再重复本节和下一节。

## 11. 通过 Sub2API 验收真实生成（历史 Build 路由）

> 本节验证的是 `sub2api -> aistudio:7860` 旧路由，不能证明当前 Playground 路由可用。
> 当前 Playground 的 Sub2API 验收按“Sub2API 指向 7870”一节执行。

必须使用当前 `/v1/models` 返回的模型 ID，不要使用历史模型名。以下命令自动取得模型，
再让 `connect.sh --test` 走 Sub2API 的 provider-scoped key：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
key=$(docker exec "$name" printenv API_KEYS | cut -d, -f1)
model=$(curl -fsS -H "Authorization: Bearer $key" http://127.0.0.1:7860/v1/models |
  python3 -c "import json,sys; print(json.load(sys.stdin)[\"data\"][0][\"id\"])")
printf "SUB2API_TEST_MODEL=%s\n" "$model"
bash connect.sh --provider aistudio --test --model "$model"
'
```

预期：

```text
SUB2API_TEST_MODEL=<当前实际模型 ID>
Sub2API: AI Proxy Kit / Aistudio: account=<id> group=<id> upstream=http://aistudio:7860
sub2api -> aistudio -> <当前实际模型 ID>: real generation PASS (PROXY_KIT_OK)
```

可选的流式路由验收：

```bash
pct exec 158 -- bash -lc '
set -e
cd /opt/ai-proxy-kit-aistudio
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
key=$(docker exec "$name" printenv API_KEYS | cut -d, -f1)
model=$(curl -fsS -H "Authorization: Bearer $key" http://127.0.0.1:7860/v1/models |
  python3 -c "import json,sys; print(json.load(sys.stdin)[\"data\"][0][\"id\"])")
bash connect.sh --provider aistudio --test --model "$model" --stream
'
```

预期最后仍是 `real generation PASS (PROXY_KIT_OK)`。源码还要求 SSE 中出现 `[DONE]` 且内容匹配，
否则不会把 receipt 记为 `PASS`（`lib/proxykit.py:309-337`）。

## 12. 验收记录

验收时记录以下非秘密字段：

| 检查项 | 合格标准 |
| --- | --- |
| LXC 状态 | `status: running` |
| 部署目录和容器工作目录 | `/opt/ai-proxy-kit-aistudio` |
| 7860 Build UI/管理 | `running`、`healthy`、`0.0.0.0:7860->7860/tcp`；仅 UI/管理 |
| 7870 Playground 监听 | `LISTEN_ADDR=0.0.0.0:7870`，容器网络可访问 |
| 7870 管理鉴权 | `ADMIN_AUTH_ENABLED=true`，匿名请求不能读取管理数据 |
| 7870 生成鉴权 | `/v1` 必须携带正确的 `PROXY_API_KEY` |
| Playground 状态 | `/api/status` 为 `RUNNING`、`accounts.ready=1` |
| Sub2API | `/health` 返回态 `ok`，`0.0.0.0:8080` 可访问 |
| 容器网络到 7870 | 从 `sub2api` 容器请求 `/v1/models` 返回 `200` |
| Playground 直连生成 | 当前模型非流式返回 `PROXY_KIT_OK` |
| Sub2API 上游 | 指向 `http://172.18.0.1:7870` |
| Sub2API 路由生成 | `sub2api -> 172.18.0.1:7870 -> <实际模型>: PROXY_KIT_OK` |

记录中只写日期、操作者、容器 ID、Compose project、模型 ID、HTTP 状态和 PASS/FAIL，不写
API Key、密码、Cookie、token、认证文件内容或完整日志。

## 常见失败定位顺序

总体顺序是：容器和工作目录 -> 端口和健康接口 -> key/控制台会话 -> `/v1/models` 和模型 ID ->
Google 账号状态 -> Sub2API 账号模式 -> 路由生成。不要从 Sub2API 页面开始猜，也不要在
`browserConnected=false` 时反复重试生成。

### 503 `Service temporarily unavailable: No available accounts.`

> 本案例来自 7860 Build 历史。Build 生成已经停用；当前 Playground 的 503、会话和模型目录
> 应以 7870 的 `/api/status`、`/v1/models` 和服务日志为准。

这个错误说明请求已经到达 AI Studio 网关，但当前没有可用的浏览器/账号。历史记录中，这类
503 与 Google 浏览器会话未恢复同时出现，并伴随以下日志：

```text
[Request] No WebSocket connection for current account #-1
[System] Browser not yet started. Initializing browser with first available account...
[System] No available accounts for recovery.
```

定位顺序：

1. 先执行本手册第 5 节，确认 7860 容器 `running`、`healthy`，工作目录正确。
2. 执行第 7 节，确认 `/v1/models` 带 key 返回 200。模型列表成功但生成 503，问题基本在账号层。
3. 执行第 8 节的 `/api/status` 摘要，确认 `browserConnected=True`，并且当前账号没有
   `isExpired` 或 `isInvalid`。
4. 查看相关日志，不打印完整环境或认证文件：

```bash
pct exec 158 -- bash -lc '
name=$(docker ps -q \
  --filter label=com.docker.compose.service=aistudio \
  --filter label=com.docker.compose.project.working_dir=/opt/ai-proxy-kit-aistudio \
  | head -n 1)
docker logs --since 30m "$name" 2>&1 |
grep -Ei "No available accounts|WebSocket|Browser not yet started|browserConnected|expired|invalid" |
tail -n 80
'
```

5. 如果 `browserConnected=false`，按第 8 节重新在 VNC 登录，或导入新的 Google 会话。
6. 如果 `browserConnected=true` 但账号标记为 expired，重新完成 Google 登录；单纯重启容器
   不会刷新失效账号。
7. 如果状态显示已连接且账号未过期、仍是 503，再检查 `currentAuthIndex` 对应的账号槽位
   （历史记录提到过 `/u/0` 与 `/u/1` 的差异）以及出口 IP 的 Google 生成资格。
8. 先重跑直连 `check.sh aistudio --model <当前模型>`，直连 PASS 后再跑第 11 节。

如果直连 `/v1/chat/completions` 没有 503，只有经 8080 返回 503，则问题在 Sub2API 路由或
Sub2API 账号状态，不在 AI Studio 的 Google 浏览器。此时检查 Sub2API 账号是否绑定正确分组、
是否可调度，以及其上游是否指向当前 Playground 的 `http://172.18.0.1:7870`；不要再使用
旧 Build 的 `http://aistudio:7860`。

### `model_not_supported`

该错误通常表示请求的模型字符串与上游当前能力或模型目录不一致。定位顺序：

1. 重新执行第 7 节，拿到本次 `/v1/models` 的真实 ID 和数量。
2. 客户端、`check.sh --model`、`connect.sh --test --model` 三处必须使用完全相同的 ID，
   包括大小写、连字符和后缀。不要使用历史记录里的 `gemini-4-hydrogen` 代替当前值。
3. 如果请求的 ID 不在当前列表中，先更新请求模型；不要通过改 Sub2API 上游 URL 绕过。
4. 如果 ID 在列表中但仍报 `model_not_supported`，检查 Sub2API 上游账号的
   `openai_responses_mode` 是否为 `force_chat_completions`。错误模式可能让上游走不兼容的
   Responses 路径。
5. 再用第 11 节显式 `--model` 复测。若直连可用、只有 Sub2API 失败，优先检查账号模式和
   路由日志；若直连也失败，问题在 AI Studio 的模型目录或 Google 账号资格。
6. 重新登录或导入会话后，模型目录可能刷新；刷新后必须重新记录并比对 `/v1/models`。

### `expired auth`

先区分两种“过期”：

- `AI Studio console session expired before the status check`：控制台登录会话失效，常见于
  `AISTUDIO_WEB_PASSWORD` 与运行容器不一致、控制台重新登录失败。
- `accountDetails[].isExpired=true`、`importedAccount=expired`：Google/AI Studio 账号本身过期。

定位顺序：

1. 执行第 8 节状态摘要，查看 `browserConnected`、`currentAuthIndex`、`expiredAccounts`、
   `invalidAccounts`、`ready`。
2. 若控制台登录失败，执行第 7 节确认 `CONSOLE_PASSWORD_MATCH=yes`；不一致时重建容器。
3. 若账号 `isExpired=true`，重新在 VNC 完成 Google 登录，或导出新的本机会话后重新导入。
   旧 Cookie 即使文件仍存在，也不能通过验收。
4. 检查 `/app/configs/auth` 挂载和 `auth-N.json` 权限；确认导入写的是当前部署使用的目录。
5. 重新读取状态，要求 `browserConnected=True`、`expiredAccounts=0`、`invalidAccounts=0`、
   `ready=True`。
6. 先跑第 9 节直连生成，再跑第 11 节 Sub2API 路由生成。只看到 `browserConnected=true`
   不能宣称通过。

## 历史证据

以下内容是仓库中已有的历史记录，用于说明错误形状和排查方向，不是本次任务的新测试结果：

- `docs/validation/windows-aistudio-proxy-20261124.md:5-24` 记录：Windows 到 LXC158 的
  `GET :7860/` 为 200，带容器 key 的 `/v1/models` 为 200；当时模型数为 1，ID 为
  `gemini-4-hydrogen`；流式生成返回 503 `No available accounts`，日志同时显示无 WebSocket
  和无可恢复账号。
- 同一记录第 85-99 行记录：`runtime/stack.env` 的 key 与运行容器 `API_KEYS` 曾不一致，
  前者请求 `/v1/models` 返回 401；恢复方向是重新登录/导入会话，并在
  `browserConnected=true` 后重测。
- `docs/VALIDATION.md:11-20` 记录另一环境中 AI Studio 网页 200、匿名 401、鉴权后 31 个模型，
  同时 `browserConnected=false`。这说明模型数量不是固定值，也说明“模型列表成功”不等于
  “Google 生成可用”。
- `docs/WINDOWS_AISTUDIO_INSTALL.md:106-116` 记录了 503 的账号侧含义、key 不一致导致 401，
  以及修改 `stack.env` 后需要重建容器。

## 源码依据

- `compose.yml:49-58`：Sub2API 发布 `0.0.0.0:8080`，容器 `SERVER_PORT=8080`。
- `compose.yml:112-130`：AI Studio 发布 `7860`，设置 `HOST=0.0.0.0`、`PORT=7860`，挂载
  `runtime/aistudio/auth` 和 `runtime/aistudio/data`。
- `lib/proxykit.py:184-190`：启动时合并 `compose.yml` 与 `compose.aistudio-novnc.yml`。
- `services/aistudio-novnc/preload.cjs:6-9`：noVNC 预加载使用 `process.cwd()` 定位
  `ui/dist`，因此容器工作目录属于部署契约。
- `scripts/connect-sub2api.py:126-148`：旧 Build 账号脚本默认写入
  `http://aistudio:7860`；当前 Playground 上游应手工改为 `http://172.18.0.1:7870`。
  新建账号时脚本还会写入 `force_chat_completions` 和 `openai_responses_supported=false`。
- `scripts/connect-sub2api.py:200-212`：测试优先从实际 `/v1/models` 选择模型，随后通过
  Sub2API 做真实生成。
- `lib/proxykit.py:340-369`：`check.sh` 验证匿名 401、鉴权模型列表、模型存在性和
  `browserConnected`，最后验证生成标记和流结束。
- `scripts/import-aistudio-session.py:110-163`：控制台登录、`/api/status` 解析、过期/失效账号
  判定和 `ready` 计算。

