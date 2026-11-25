# Windows 本机安装 AI Studio 反代（2026-11-25）

本文记录在 Windows 11 本机通过仓库自带入口安装、验收 AI Studio 反向代理的完整流程。
适用于「Windows 上双击使用，Docker 跑在 WSL2」的默认拓扑。

## 结论

Windows 原生入口 `install.cmd aistudio` 可以完成安装，不需要第二套实现：

| 检查项 | Windows 端实测 |
| --- | --- |
| `GET http://127.0.0.1:7860/` | **200** |
| `GET /v1/models`（无凭据） | **401**（预期，反代必须拒绝匿名） |
| `GET /v1/models`（`stack.env` 的 `AISTUDIO_API_KEY`） | **200，31 个模型** |
| `GET /health` | **200**，`browserConnected=false` |

`browserConnected=false` 表示 Google 登录会话缺失，属于账号状态，不是反代安装失败。
真实生成需要先在 AI Studio 控制台完成本人 Google 登录。

## 前置条件

- Windows 10/11，已启用 WSL2。
- 一个可用的 Linux 发行版（本文为 `Ubuntu-22.04`），其中 Docker Engine 可由当前用户访问。
- 仓库位于 Windows 可见路径或 WSL UNC 路径均可；入口脚本会自动把 Windows 路径翻译成 WSL 路径。

先确认 Docker 可达：

```powershell
wsl -d Ubuntu-22.04 --exec docker info
```

若 Docker 不可达，先启动 WSL 内的 Docker（或启用 Docker Desktop 的 WSL 集成），再继续。

## 安装

在仓库根目录双击 `install.cmd`，或显式指定服务：

```powershell
.\install.cmd aistudio
```

等价于：

```powershell
.\proxykit.ps1 -Action install -Service aistudio -Distribution Ubuntu-22.04
```

安装会依次完成：

1. 读取或生成 `runtime/stack.env`（权限 600），已有密钥保留，不重置账号。
2. 复用本机已缓存镜像 `ibuhub/aistudio-to-api:v1.4.1`；缺失时才拉取。
3. `docker compose up -d aistudio`，重建容器使配置与 `stack.env` 一致。
4. 轮询 `/health` 直到服务就绪。
5. 从 `stack.env` 派生 `runtime/LOCAL_ACCESS.txt` 和 `runtime/open-aistudio.html`。

第 4、5 步是关键：容器是否健康要看服务端口，凭据提示必须由配置生成，
不能维护手写快照，否则轮换密钥后提示会失真。

## 验收

Windows 端只读检查：

```powershell
.\check.cmd aistudio
```

预期输出：

```powershell
.\check.cmd aistudio
# aistudio: auth PASS; 31 model(s).
# AI Studio browserConnected=False; complete Google login in its Web/VNC UI.
```

`check.cmd` 会验证三件事：匿名请求被拒绝（401）、带 key 请求成功、模型列表非空。
它不会打印密钥。

手动复现（key 只放在内存变量）：

```powershell
$key = (Select-String -Path .\runtime\stack.env -Pattern '^AISTUDIO_API_KEY=').Line -replace '^AISTUDIO_API_KEY=',''
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7860/v1/models -Headers @{ Authorization = "Bearer $key" }
```

## 打开控制台

双击 `open-aistudio.cmd`：它先从 `stack.env` 重新生成登录页，再用默认浏览器打开
`runtime/open-aistudio.html`，页面会自动提交当前控制台密码到 `/login`。

这条路径的好处是不需要手工复制密码；代价是 `runtime/open-aistudio.html` 含明文口令，
因此它位于被 `.gitignore` 忽略的 `runtime/` 下，不要提交、不要外发。

## 完成 Google 登录

1. 打开控制台后进入 VNC / 浏览器界面。
2. 用本人 Google 账号登录 AI Studio。
3. 等待 `/health` 返回 `browserConnected=true`。
4. 再验证一次真实生成：

```powershell
$key = (Select-String -Path .\runtime\stack.env -Pattern '^AISTUDIO_API_KEY=').Line -replace '^AISTUDIO_API_KEY=',''
$body = '{"model":"gemini-4-hydrogen","messages":[{"role":"user","content":"Reply OK"}],"max_tokens":16,"stream":true}'
curl.exe -sS -N --max-time 180 -H "Authorization: Bearer $key" -H "Content-Type: application/json" -d $body http://127.0.0.1:7860/v1/chat/completions
```

浏览器会话未恢复时，该请求会返回 `503 Service temporarily unavailable: No available accounts`。
这是账号侧证据，不是端口或安装问题。

## 常见误判

| 现象 | 含义 |
| --- | --- |
| `/v1/models` 返回 401 | 未带 key，或 key 与容器 `API_KEYS` 不一致。用 `check.cmd aistudio` 复测。 |
| `/health` 200 但生成 503 | 服务正常，Google 会话缺失；去控制台登录。 |
| 双击 `install.cmd` 后端口无响应 | 先看 `wsl -d Ubuntu-22.04 --exec docker ps` 是否有 `ai-proxy-kit-aistudio-1`。 |
| 改了 `stack.env` 但容器仍旧 key | `install` 会重建容器；只改文件不会自动生效。 |

## 未改动范围

- 不修改其他服务的镜像、端口或卷。
- 不自动登录 Google，不注入、不保存任何 Google 凭据。
- 不把 `runtime/` 下的任何密钥写入仓库或日志。
