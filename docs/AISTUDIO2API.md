# AI Studio Playground 服务（AIStudio2API）

仓库的 AI Studio 入口只有一个 Compose 服务：`aistudio`。它运行
`ghcr.io/mag1cfall/aistudio2api:latest`，只使用 Playground 通道，容器端口为 `2048`。
旧 Build 通道已经停用；7860 的 Build 反代、noVNC 自托管和旧会话导入脚本都不在当前部署中。

本文说明当前服务配置、会话导入、账号就绪判据、真实生成和 Sub2API 接入。历史迁移实测见
[AI Studio Playground 迁移验证](validation/aistudio-playground-migration-20261125.md)。
本文不记录任何真实 key、邮箱、Cookie 或管理员密码，也不声称更新文档时重新部署过。

## 服务契约

| 项目 | 当前值 |
| --- | --- |
| Compose 服务名 | `aistudio` |
| 镜像 | `ghcr.io/mag1cfall/aistudio2api:latest`，可用 `AISTUDIO_IMAGE` 覆盖 |
| 容器端口 | `2048`，`LISTEN_ADDR=0.0.0.0:2048` |
| 默认宿主端口 | `2048`，可用 `AISTUDIO_PORT` 覆盖 |
| Compose 内地址 | `http://aistudio:2048` |
| 数据目录 | 宿主机 `./runtime/aistudio/data` -> 容器 `/data` |
| 生成通道 | `UPSTREAM_CHANNELS=playground` |
| 管理鉴权 | `ADMIN_AUTH_ENABLED=true`，用户名 `ADMIN_USERNAME` |
| 生成鉴权 | 容器 `PROXY_API_KEY`，对应宿主配置 `AISTUDIO_API_KEY` |

`AISTUDIO_PORT` 只改宿主机发布端口。容器和 Compose 网络里的端口始终是 `2048`，所以
Compose 内其他服务应连接 `http://aistudio:2048`。例如 LXC 现有部署把宿主端口保留为
`7870` 时，映射是 `0.0.0.0:7870 -> 容器 2048`；`7870` 不是另一个服务端口。

## 配置映射

`runtime/stack.env` 使用仓库前缀，Compose 将它映射为 AIStudio2API 的容器变量：

| `runtime/stack.env` | 容器变量 | 作用 |
| --- | --- | --- |
| `AISTUDIO_API_KEY` | `PROXY_API_KEY` | `/v1` 的 Bearer key，也是 Sub2API 上游 key |
| `AISTUDIO_ADMIN_USER` | `ADMIN_USERNAME` | 管理界面和 `/api` 登录用户名，默认 `admin` |
| `AISTUDIO_ADMIN_PASSWORD` | `ADMIN_PASSWORD` | 管理界面和 `/api` 登录密码，必填 |
| `AISTUDIO_CHANNELS` | `UPSTREAM_CHANNELS` | 生成通道，使用 `playground` |
| `AISTUDIO_ADMIN_AUTH` | `ADMIN_AUTH_ENABLED` | 管理鉴权开关，默认 `true` |
| `AISTUDIO_IMAGE` | 镜像 | 可选镜像覆盖 |

`AISTUDIO_API_KEY` 和 `AISTUDIO_ADMIN_PASSWORD` 由安装流程生成或保留，统一保存在权限
`600` 的 `runtime/stack.env`。不要把变量值写进文档、聊天记录、截图或命令输出。

从旧 Build 部署升级时，先运行一次安装流程：

```bash
bash install.sh aistudio
```

它会补全缺失的 Playground 变量，并将旧的 `AISTUDIO_WEB_PASSWORD` 迁移为
`AISTUDIO_ADMIN_PASSWORD`、把旧的 `AISTUDIO_PORT=7860` 改为 `2048`。LXC 需要继续使用宿主
端口 `7870` 时，首次安装显式传入 `--aistudio-port 7870`。只运行 `start.sh` 不会执行这项
配置迁移。

## 安装与启动

在仓库根目录执行：

```bash
# 安装并启动 AI Studio。
bash install.sh aistudio

# 日常单独启动或一次启动四个服务。
bash start.sh aistudio
bash start.sh all
```

Windows 双击入口或 PowerShell 等价命令：

```powershell
.\install.cmd aistudio
.\start.cmd aistudio
```

首次安装会创建 `runtime/aistudio/data`、生成或保留密钥、准备镜像并等待
`http://127.0.0.1:2048/` 可达，然后刷新 `runtime/LOCAL_ACCESS.txt` 和
`runtime/open-aistudio.html`。日常启动复用本机镜像。

## 基础可达性

先看容器和宿主端口。默认配置下：

```bash
docker compose --env-file runtime/stack.env --profile aistudio ps aistudio
docker compose --env-file runtime/stack.env --profile aistudio port aistudio 2048
curl -sS -o /dev/null -w 'root_http=%{http_code}\n' http://127.0.0.1:2048/
```

端口检查应得到 `0.0.0.0:2048`；LXC 自定义宿主端口时应得到 `0.0.0.0:7870`。
根页面返回非 5xx 只表示 Web 管理进程可达。AIStudio2API 不提供 `/health`，因此不能用
`/health` 判断账号或生成是否就绪。

检查 `/v1` 鉴权和模型列表：

```bash
bash check.sh aistudio
```

合格输出形状是：

```text
aistudio: auth PASS; <N> model(s).
```

`check.sh` 会先确认匿名 `/v1/models` 返回 `401`，再使用 `AISTUDIO_API_KEY` 请求；只有
返回的模型列表非空才算通过。模型数量不是固定值，应以本次响应为准。

## 导入 Google 会话

Playground 需要一份已经登录 Google 的 Playwright storage-state JSON。Windows 入口默认从
Downloads 读取 `ai-proxy-kit-google-session.json`，在 WSL 中复权后调用容器内的
`aistudio2api setup`。Compose 服务的 `/data` 对应 `runtime/aistudio/data`，所以显式传入
该目录：

```powershell
.\import-aistudio2api-session.cmd -DataDir .\runtime\aistudio\data
```

运行本仓库的 Compose 栈时不要省略 `-DataDir`：脚本的默认目录是
`runtime/aistudio`，而服务实际挂载到 `/data` 的是 `runtime/aistudio/data`。

指定导出文件和容器时：

```powershell
.\import-aistudio2api-session.cmd `
  -Source "C:\path\to\google-session.json" `
  -Distribution "Ubuntu-22.04" `
  -DataDir ".\runtime\aistudio\data" `
  -Container "ai-proxy-kit-aistudio-1"
```

入口会把文件复制为宿主机 `runtime/aistudio/data/session.json`，限制为 `600`，再执行：

```text
aistudio2api setup --storage-state /data/session.json
```

导入过程不会打印 Cookie 或会话正文。`/data` 的实际挂载以 `compose.yml` 为准；如果改了
服务名、Compose project 或数据目录，`-Container` 和 `-DataDir` 必须同步修改。

Linux 或 LXC 可以手动完成同一流程：

```bash
install -m 600 /path/to/google-session.json runtime/aistudio/data/session.json
docker compose --env-file runtime/stack.env --profile aistudio \
  exec -T aistudio aistudio2api setup --storage-state /data/session.json
rm -f runtime/aistudio/data/session.json
```

导入成功后删除 Downloads 中的导出副本和临时 `session.json`。`runtime/aistudio/data` 里的
运行态仍含账号凭据，必须保持本机私有，不得提交或打包发布。

## 账号就绪与真实生成

先从当前 `/v1/models` 选一个模型，然后让 `check.sh` 同时检查匿名 401、鉴权模型列表、
管理端账号状态和真实生成：

```bash
port=$(grep '^AISTUDIO_PORT=' runtime/stack.env | cut -d= -f2- | tr -d "'")
port=${port:-2048}
key=$(grep '^AISTUDIO_API_KEY=' runtime/stack.env | cut -d= -f2- | tr -d "'")
model=$(curl -fsS -H "Authorization: Bearer $key" \
  "http://127.0.0.1:${port}/v1/models" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); m=d.get("data") or d.get("models"); assert m; print(m[0]["id"])')
printf 'ACCEPTANCE_MODEL=%s\n' "$model"
bash check.sh aistudio --model "$model"
```

最后两段输出应出现类似内容：

```text
AI Studio console: <ready>/<total> account(s) ready (RUNNING).
aistudio: real non-streaming generation PASS (PROXY_KIT_OK)
```

`check.sh --model` 会登录 `/api/auth/login` 并读取 `/api/status`，但不会打印密码。账号就绪
至少要求 `state` 为 `RUNNING`、`running=true`、`accounts.ready>=1`。容器运行、根页面 200、
模型列表非空或 `accounts.total=1` 都不能单独证明可以生成。

也可以直接发最小非流式请求：

```bash
curl -fsS \
  -H "Authorization: Bearer $key" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply exactly PROXY_KIT_OK\"}],\"stream\":false}" \
  "http://127.0.0.1:${port}/v1/chat/completions" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); t=d["choices"][0]["message"]["content"]; assert "PROXY_KIT_OK" in t, t; print("generation=PASS")'
```

只有最后输出 `generation=PASS` 才能记录为本次真实生成通过。匿名或错误 key 请求应被拒绝；
账号未就绪、会话过期、额度不足或模型 ID 不属于当前列表时，不应把失败解释成端口故障。

## 接入 Sub2API

同一 Compose 栈内，`connect.sh` 使用服务名连接：

```bash
bash connect.sh --provider aistudio
bash connect.sh --provider aistudio --test
```

AI Studio 上游的默认地址应为 `http://aistudio:2048`，key 使用
`AISTUDIO_API_KEY`（即容器的 `PROXY_API_KEY`）。`--test` 会选择当前模型列表中的模型，
通过 Sub2API 的 provider key 做真实生成；只有返回 `PROXY_KIT_OK` 才算路由通过。

如果 Sub2API 不在同一 Compose 网络，才使用宿主机发布地址，例如 LXC 上：

```bash
bash connect.sh --provider aistudio --test \
  --upstream-url http://172.18.0.1:7870
```

`7870` 是宿主机端口，`172.18.0.1` 是当前 LXC 环境中从容器访问宿主机的地址；这两个值都
要按实际环境核对。不要在同一 Compose 栈里改用宿主 IP，也不要在容器内填写
`127.0.0.1`。

## 排障

| 现象 | 检查方向 |
| --- | --- |
| 容器启动失败或变量缺失 | 先运行 `bash install.sh aistudio`，确认 `runtime/stack.env` 有 `AISTUDIO_API_KEY` 和 `AISTUDIO_ADMIN_PASSWORD` |
| 根页面不可达 | 检查 `docker compose ... ps aistudio`、宿主端口冲突和日志 |
| `/v1/models` 返回 401 | 客户端 key 与容器 `PROXY_API_KEY` 不一致；用 `bash check.sh aistudio` 复测 |
| `check.sh --model` 报管理登录失败 | 核对 `AISTUDIO_ADMIN_USER` / `AISTUDIO_ADMIN_PASSWORD`，重建容器使配置生效 |
| `/api/status` 不是 `RUNNING` 或 `accounts.ready=0` | 先导入有效会话；必要时在 Playground 管理界面重新完成本人 Google 登录 |
| 生成返回 503 或账号不可用 | 这是账号侧问题，先看 `/api/status`，不要只重启容器 |
| Sub2API 无法连接 2048 | 同栈使用 `http://aistudio:2048`；跨栈才用宿主机地址和端口 |

配置改动后必须重建 `aistudio` 容器。只编辑 `runtime/stack.env` 不会让运行中的容器自动
加载新值。
