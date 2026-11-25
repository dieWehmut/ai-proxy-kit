# Windows 本机 AI Studio 反代测试（2026-11-24）

> **历史：Build 通道已停用。** 本记录保留 2026-11-24 的 7860/noVNC 排障证据，不再作为
> 当前部署或验收指引。Playground 与端口迁移见
> [aistudio-playground-migration-20261125.md](aistudio-playground-migration-20261125.md)。

## 结论

Windows 本机到 LXC158 的 AI Studio HTTP 反代链路正常：

- `GET http://192.168.50.10:7860/` 返回 **200**。
- 使用运行容器中的 API key 请求 `GET /v1/models` 返回 **200**，模型数为 **1**，模型 ID 为 `gemini-4-hydrogen`。
- noVNC 自托管资源已在同一容器生效：浏览器加载了 **45** 个本地 noVNC/pako 模块请求，**0** 个 `esm.sh` 请求。

真实生成当前仍被账号/浏览器会话阻塞，不能宣称生成成功：

- 非流式 `POST /v1/chat/completions` 在 Windows 侧 60 秒超时；容器日志显示客户端在 60 秒时主动断开，上游请求仍在运行。
- 改用 Windows `curl.exe` + `stream=true` 后返回 **HTTP 503**：
  ```json
  {"error":{"code":503,"message":"Service temporarily unavailable: No available accounts.","type":"service_unavailable"}}
  ```
- 同一时段容器日志显示：
  ```text
  [Request] No WebSocket connection for current account #-1
  [System] Browser not yet started. Initializing browser with first available account...
  [System] No available accounts for recovery.
  ```
- 这与仓库中既有的 `browserConnected=false` 记录一致：Google/AI Studio 浏览器会话未恢复，不是 Windows 到反代的 TCP/HTTP 不通。

## 环境

| 项目 | 值 |
|---|---|
| Windows 主机 | `192.168.0.105` |
| WSL 入口 | `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`、`/mnt/c/Windows/System32/curl.exe` |
| LXC158 | `192.168.50.10`，PVE `root@192.168.10.2`，`pct exec 158` |
| AI Studio 旧栈 | 容器 `ai-proxy-kit-aistudio-aistudio-1`，`ibuhub/aistudio-to-api:v1.4.1`，`0.0.0.0:7860->7860/tcp`，healthy |
| 新 Playground | `aistudio2api-playground-aistudio2api-1`，仅 `127.0.0.1:7870`，不在本次 Windows 直连范围 |

## 复现命令

### 1. Windows 根路径和端口

```powershell
Test-NetConnection 192.168.50.10 -Port 7860
Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 http://192.168.50.10:7860/
```

### 2. Windows 模型列表

从运行容器读取 key，不打印：

```bash
SSH=/mnt/c/Windows/System32/OpenSSH/ssh.exe
KEY=$("$SSH" -o BatchMode=yes root@192.168.10.2 \
  "pct exec 158 -- bash -lc 'docker exec ai-proxy-kit-aistudio-aistudio-1 sh -lc \"printenv API_KEYS\"'" \
  | sed 's/\x1b\[[0-9;]*m//g' | tr -d '\r' | grep -v 'TERM environment' | sed '/^[[:space:]]*$/d' | tail -1 | cut -d, -f1)
```

然后由 Windows PowerShell 发起请求（key 只在内存变量，不写入文档）：

```powershell
$h = @{ Authorization = 'Bearer ' + $env:AISTUDIO_TEST_KEY }
Invoke-WebRequest -UseBasicParsing -Headers $h http://192.168.50.10:7860/v1/models
```

实测 `status=200`、`models=1`、`model=gemini-4-hydrogen`。

### 3. 非流式 / 流式生成

非流式（60 秒客户端超时）：

```powershell
$body = @{ model='gemini-4-hydrogen'; messages=@(@{role='user'; content='Reply OK'}); max_tokens=16; stream=$false } | ConvertTo-Json -Depth 6
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $h -ContentType 'application/json' -Body $body http://192.168.50.10:7860/v1/chat/completions
```

流式（Windows curl）：

```bash
/mnt/c/Windows/System32/curl.exe -sS -N --max-time 180 \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gemini-4-hydrogen","messages":[{"role":"user","content":"Reply OK"}],"max_tokens":16,"stream":true}' \
  http://192.168.50.10:7860/v1/chat/completions
```

实测流式返回 HTTP 503 `No available accounts`。

## API key 一致性注意

本机 `runtime/stack.env` 中的 `AISTUDIO_API_KEY` 与当前运行容器 `API_KEYS` **不一致**：

- 用 `runtime/stack.env` 的 key 请求 `/v1/models`：**401**。
- 用容器 `API_KEYS` 的 key：**200**。

因此客户端应以容器实际 `API_KEYS` 为准；不要把任一 key 写入仓库或日志。本次测试后已将本地 `runtime/stack.env` 的 `AISTUDIO_API_KEY` 更新为与容器 `API_KEYS` 一致；该文件被 `.gitignore` 忽略，不进入 commit。

## 恢复真实生成的最小步骤

1. 在 LXC158 的 AI Studio 页面/VNC 中重新完成 Google 登录，或导入新的有效 `storageState`。
2. 等待容器日志出现有效 WebSocket 连接和 `browserConnected=true`。
3. 重新执行 `/v1/models` 与一次最小 `chat/completions`，确认不再出现 `No available accounts`。
4. 若仍未恢复，检查 `auth-1.json` 的账号槽位（`/u/0` vs `/u/1`）和出口 IP 的 Google 生成资格。

## 未改动范围

- 旧 7860 容器镜像、端口和网络未改。
- 新 7870 Playground 未改，仍仅绑定 `127.0.0.1`。
- LXC152/153 未改。
- 未打印或提交任何 API key、cookie、token。
