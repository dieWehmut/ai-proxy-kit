# Windows 本机安装 AI Studio Playground（2026-11-25）

本文说明在 Windows 11 上通过仓库自带入口安装和验收 AI Studio 反向代理。默认拓扑是
Windows 双击运行入口，Docker 运行在 WSL2 中，AI Studio 使用 Compose 服务 `aistudio`、
AIStudio2API 镜像和 Playground 通道。

当前容器端口与默认宿主端口都是 `2048`。本文不包含已停用的 Build 通道或旧导入流程；
这些入口已经删除。文档只描述当前命令和验收判据，不声称本次更新已在本机重新部署或完成
真实生成。

## 前置条件

- Windows 10/11，已启用 WSL2。
- 一个可用的 Linux 发行版，例如 `Ubuntu-22.04`；其中的 Docker Engine 可由当前用户访问。
- 仓库位于 Windows 路径或 WSL UNC 路径均可。入口脚本通过 `wslpath` 转换路径。

先确认 Docker 可达：

```powershell
wsl -d Ubuntu-22.04 --exec docker info
```

若 Docker 不可达，先启动 WSL 内的 Docker Engine，或启用 Docker Desktop 的 WSL 集成。

## 安装

在仓库根目录双击 `install.cmd`，或显式安装 AI Studio：

```powershell
.\install.cmd aistudio
```

等价于：

```powershell
.\proxykit.ps1 -Action install -Service aistudio -Distribution Ubuntu-22.04
```

安装流程会：

1. 读取或创建权限 `600` 的 `runtime/stack.env`，保留已有密钥。
2. 补齐 `AISTUDIO_ADMIN_PASSWORD`，并把旧部署的 AI Studio 宿主端口迁移到当前默认
   `2048`。
3. 使用或拉取 `ghcr.io/mag1cfall/aistudio2api:latest`。
4. 执行 `docker compose up -d aistudio`，等待 `http://127.0.0.1:2048/` 可达。
5. 生成 `runtime/LOCAL_ACCESS.txt` 和 `runtime/open-aistudio.html`。

当前 Compose 配置有四个服务：Antigravity `antigravity`、WorkBuddy `workbuddy`、
AI Studio `aistudio` 和 Sub2API `sub2api`。只安装 AI Studio 使用
`.\install.cmd aistudio`；安装全部使用 `.\install.cmd all`。日常启动使用：

```powershell
.\start.cmd aistudio
```

Linux/WSL 中等价命令：

```bash
bash start.sh aistudio
bash start.sh all
```

## 验收鉴权和模型列表

只读检查：

```powershell
.\check.cmd aistudio
```

合格输出形状：

```text
aistudio: auth PASS; <N> model(s).
```

它验证三件事：匿名 `/v1/models` 返回 `401`、使用 `AISTUDIO_API_KEY` 请求成功、模型列表
非空。它不会打印 key。模型数量不是固定值，不能把历史记录的模型数当作当前结果。

手动复测时，key 只放进当前 PowerShell 进程的内存：

```powershell
$key = (Select-String -Path .\runtime\stack.env -Pattern '^AISTUDIO_API_KEY=').Line `
  -replace "^AISTUDIO_API_KEY='", '' -replace "'$", ''
Invoke-WebRequest -UseBasicParsing -Headers @{ Authorization = "Bearer $key" } `
  http://127.0.0.1:2048/v1/models
```

AIStudio2API 没有 `/health`。根页面返回非 5xx 只说明 Web 管理进程可达；账号是否就绪必须
查看管理端 `/api/status`。

## 打开控制台

双击 `open-aistudio.cmd`，或运行：

```powershell
.\open-aistudio.cmd
```

入口先刷新 `runtime/LOCAL_ACCESS.txt` 和 `runtime/open-aistudio.html`，再用默认浏览器打开
Playground 控制台。管理登录信息来自 `runtime/stack.env`：

- 用户名：`AISTUDIO_ADMIN_USER`，默认 `admin`；
- 密码：`AISTUDIO_ADMIN_PASSWORD`。

`runtime/LOCAL_ACCESS.txt` 含明文凭据，只用于本机查看，不要提交、截图或外发。

## 导入已登录的 Google 会话

如果 Windows 浏览器已经登录 Google，可导出 Playwright storage-state JSON，默认文件名为
`ai-proxy-kit-google-session.json`，放在 Downloads。导入入口是
`import-aistudio2api-session.cmd`。

Compose 服务把 `./runtime/aistudio/data` 挂载为容器 `/data`，因此显式传入同一目录：

```powershell
.\import-aistudio2api-session.cmd -DataDir .\runtime\aistudio\data
```

运行本仓库的 Compose 栈时不要省略 `-DataDir`：脚本的默认目录是
`runtime/aistudio`，而服务实际挂载到 `/data` 的是 `runtime/aistudio/data`。

指定导出文件或 WSL 发行版：

```powershell
.\import-aistudio2api-session.cmd `
  -Source "C:\path\to\google-session.json" `
  -Distribution "Ubuntu-22.04" `
  -DataDir ".\runtime\aistudio\data"
```

脚本会复制为 `runtime/aistudio/data/session.json`、限制为 `600`，然后在容器内执行：

```text
aistudio2api setup --storage-state /data/session.json
```

默认容器名是 `ai-proxy-kit-aistudio-1`；如果部署改变了 Compose project 或服务名，使用
`-Container` 指定实际容器。导入过程不会输出 Cookie 或会话正文。成功后删除 Downloads
中的导出副本和临时的 `runtime/aistudio/data/session.json`。

导入成功不等于账号可以生成。导入后仍要通过 `/api/status` 和管理端账号状态确认。

## 账号就绪与真实生成

先从当前模型列表取一个 ID，再让 `check.cmd` 完成鉴权、账号状态和真实生成检查：

```powershell
$key = (Select-String -Path .\runtime\stack.env -Pattern '^AISTUDIO_API_KEY=').Line `
  -replace "^AISTUDIO_API_KEY='", '' -replace "'$", ''
$models = Invoke-RestMethod -Headers @{ Authorization = "Bearer $key" } http://127.0.0.1:2048/v1/models
$model = $models.data[0].id
.\check.cmd aistudio --model $model
```

合格输出形状：

```text
aistudio: auth PASS; <N> model(s).
AI Studio console: <ready>/<total> account(s) ready (RUNNING).
aistudio: real non-streaming generation PASS (PROXY_KIT_OK)
```

`check.cmd --model` 会使用 `AISTUDIO_ADMIN_USER` / `AISTUDIO_ADMIN_PASSWORD` 登录管理端，
读取 `/api/status`，但不会打印密码。账号就绪至少要求 `state=RUNNING`、`running=true`、
`accounts.ready>=1`。只有最后一行出现 `PROXY_KIT_OK` 才能把真实生成记为通过。

直接复测生成时使用当前模型，不要把历史模型名写死：

```powershell
$body = @{
  model = $model
  messages = @(@{ role = 'user'; content = 'Reply exactly PROXY_KIT_OK' })
  stream = $false
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $key" } `
  -ContentType 'application/json' -Body $body http://127.0.0.1:2048/v1/chat/completions
```

如果账号尚未准备好，请求可能返回 503 或类似的无可用账号错误。这是账号/会话侧证据；
先检查 `/api/status`，不要只重启容器。

## 常见问题

| 现象 | 检查方向 |
| --- | --- |
| `install.cmd aistudio` 后 2048 无响应 | 运行 `wsl -d Ubuntu-22.04 --exec docker ps`，查看 `ai-proxy-kit-aistudio-1` 和 Compose 日志 |
| `/v1/models` 返回 401 | 客户端 key 与容器 `PROXY_API_KEY` 不一致；重新运行 `.\check.cmd aistudio` |
| 管理登录失败 | 检查 `AISTUDIO_ADMIN_USER` / `AISTUDIO_ADMIN_PASSWORD`，重建容器 |
| `/api/status` 不是 `RUNNING` | 在 Playground 控制台启动生成服务，或检查 `AUTO_START` 和日志 |
| `accounts.ready=0` 或生成 503 | 导入有效的 Google storage-state，必要时重新导出 |
| 修改 `stack.env` 后配置未生效 | 重新运行安装或启动入口；只改文件不会更新已运行容器 |

## 数据与安全

- 宿主机数据目录是 `runtime/aistudio/data`，容器内为 `/data`。
- `runtime/` 已被 Git 忽略；不要把 `stack.env`、会话、Cookie、账号信息或 `LOCAL_ACCESS.txt`
  提交到仓库。
- 不自动登录 Google，不注入真实凭据。会话导入只接受本机受控的 storage-state 文件。
- 不升级或改写其他服务的镜像、端口和卷。
