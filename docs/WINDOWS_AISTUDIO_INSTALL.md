# Windows 本机安装 AI Studio 反代（2026-11-25）

本文记录在 Windows 11 本机通过仓库自带入口安装、验收 AI Studio 反向代理的完整流程。
适用于「Windows 上双击使用，Docker 跑在 WSL2」的默认拓扑。

## 结论

Windows 原生入口 `install.cmd aistudio` 可以完成安装，不需要第二套实现：

| 检查项 | 已有安装记录（非本次文档更新复跑） |
| --- | --- |
| `GET http://127.0.0.1:7860/` | **200** |
| `GET /v1/models`（无凭据） | **401**（预期，反代必须拒绝匿名） |
| `GET /v1/models`（`stack.env` 的 `AISTUDIO_API_KEY`） | **200，31 个模型** |
| `GET /health` | **200**，`browserConnected=false` |

`browserConnected=false` 表示浏览器会话尚未建立，属于账号状态，不是反代安装失败。
但反过来并不成立：`browserConnected=true` 也不能单独证明可以生成。

## 先分清「进程启动」和「账号就绪」

`/health` 和 `/api/status` 都可以包含 `browserConnected`，但它们的证明力不同：

- `browserConnected=true` 只说明 AI Studio 的浏览器进程已经启动并与服务建立了连接。
  它不保证已经选中账号，也不保证账号会话仍有有效凭据。
- `/api/status` 才是账号就绪的判定依据。真实就绪至少要同时满足：
  - `browserConnected=true`；
  - `isSystemBusy=false`；
  - `currentAuthIndex` 是 `>= 0` 的整数；
  - `accountDetails` 中存在相同的 `index`；
  - 该账号的 `isExpired=false` 且 `isInvalid=false`；
  - 如果刚导入了 `auth-N.json`，该账号还应被服务识别为已导入。

有时 `/api/status` 会把上述字段放在顶层，有时会包在 `status` 对象中；两种形式都要按同一组
条件判断。若请求被重定向到 `/login`，说明控制台密码或登录会话无效，同样不能算就绪。

导入脚本会按这些条件轮询 `/api/status`，并输出一行安全摘要，例如
`browserConnected=true, currentAuthIndex=0, ..., ready=true`。这只是字段格式示例，不是本次
文档更新的运行结果。只有 `ready=true` 才表示账号侧已经就绪；即便如此，也不能据此声称
真实生成已经成功，仍应单独发起生成请求验证。

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
它不会打印密钥。注意它最后一行只报告 `/health` 的 `browserConnected`，因此即使显示
`browserConnected=true`，也只代表浏览器进程起来了；还要按下一节的方法确认
`/api/status` 中的账号索引和过期状态。

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

## 导入已登录的 Google 会话

如果希望复用 Windows 本机浏览器中已有的 Google 登录，可先用
`tools/google-session-extension` 导出会话文件，再在仓库根目录执行导入。扩展用法和
导出安全要求见 `docs/AISTUDIO_LOCAL_LOGIN.md`。文档中的会话路径、账号名和密码都只是占位符；
不要把真实 Cookie、API key 或邮箱写进本文或提交到仓库。

Windows 一键入口是 `import-aistudio-session.cmd`。它通过 WSL 调用仓库内的 Python 脚本，
默认读取 `%USERPROFILE%\Downloads\ai-proxy-kit-google-session.json`，默认使用
`runtime/stack.env`、`runtime/aistudio/auth` 和 `http://127.0.0.1:<AISTUDIO_PORT>`：

```powershell
.\import-aistudio-session.cmd
```

也可以把导出的 JSON 路径作为第一个参数传入：

```powershell
.\import-aistudio-session.cmd "C:\path\to\ai-proxy-kit-google-session.json"
```

一键入口不打印会话内容；导入后它会调用控制台登录接口并查询 `/api/status`。查看下面的
`ready` 值，不要只看 `/health` 的 `browserConnected`。

需要在 WSL/Linux 中覆盖部署路径或控制台地址时，直接运行 Python 脚本：

```bash
cd /path/to/ai-proxy-kit
python3 scripts/import-aistudio-session.py /path/to/ai-proxy-kit-google-session.json \
  --auth-dir runtime/aistudio/auth \
  --base-url http://127.0.0.1:7860 \
  --ready-timeout 120
```

主要参数如下：

| 参数 | 作用与注意点 |
| --- | --- |
| `--auth-dir DIR` | 写入 `auth-N.json` 的本机目录，默认 `runtime/aistudio/auth`。该目录在 `compose.yml` 中挂载为容器内的 `/app/configs/auth`，自定义目录时应同步调整部署挂载。脚本会把目录权限设为 `700`、会话文件权限设为 `600`；重复导入同一份会话会复用已有文件。 |
| `--base-url URL` | AI Studio 控制台地址，默认根据 `stack.env` 的 `AISTUDIO_PORT` 生成 `http://127.0.0.1:<AISTUDIO_PORT>`。必须是 `http`/`https` 地址，可带路径，但不能包含用户名、密码、查询串或片段。 |
| `--ready-timeout SECONDS` | 导入后等待账号就绪的最长秒数，默认 `60`。脚本会在时限内轮询 `/api/status`，直到 `ready=true`；负数会被拒绝，`0` 表示只做一次即时检查。超时返回非零，并保留已写入的会话文件供修复后重试。若导入的账号已过期或无效，脚本会立即报告，不会等到超时。 |
| `--wait SECONDS` | 等待导出文件出现的秒数，默认 `0`。它与 `--ready-timeout` 不同：前者只等文件生成，后者才是在等浏览器会话和账号就绪。 |
| `--env-file FILE` | 指定读取控制台密码和端口的 env 文件，默认 `runtime/stack.env`。 |

脚本成功写出会话并不等于账号可用。若输出 `ready=false`，先看
`currentAuthIndex`、`isExpired`、`isInvalid` 和 `importedAccount` 字段；通常需要在
AI Studio 的 Web/VNC 控制台重新完成本人登录，或重新导出更新后的会话。

## noVNC 使用本地 vendored 资源

AI Studio 的 VNC 登录页不再依赖 `esm.sh`。仓库把 noVNC 1.4.0 放在
`services/aistudio-novnc/vendor/novnc/`，对应的浏览器入口是
`/vendor/novnc/core/rfb.js`。许可证和来源记录在同目录的 `PROVENANCE.md` 与
`LICENSE.txt` 中。

启动时的工作方式：

1. `compose.aistudio-novnc.yml` 把 `services/aistudio-novnc` 以只读方式挂载到容器的
   `/app/proxy-kit`。
2. `preload.cjs` 调用 `novnc.cjs`，把本地 noVNC 树复制到
   `/app/ui/dist/vendor/novnc`。
3. 同一适配器扫描 AI Studio 的 `ui/dist/assets`，把原来指向
   `https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js` 的导入改写为
   `/vendor/novnc/core/rfb.js`。
4. 如果本地 vendor 树不完整，或找不到需要改写的 noVNC 导入，容器会直接失败，避免在
   不知情的情况下回退到 CDN。

验收时在打开 VNC 页面的浏览器开发者工具 Network 面板重载页面：应能看到
`/vendor/novnc/core/rfb.js` 等同源请求，并且 `esm.sh` 请求数为 0。容器日志也会输出
`[AIProxyKit] noVNC self-host assets ready`。这条检查只证明前端资源来自本地，不代表
Google 登录或真实生成已经成功。

## 完成 Google 登录

1. 打开控制台后进入 VNC / 浏览器界面。
2. 用本人 Google 账号登录 AI Studio。
3. 不要停在 `/health` 的 `browserConnected=true`；重新运行导入脚本，或查询
   `/api/status`，确认 `browserConnected=true`、`isSystemBusy=false`，且
   `currentAuthIndex` 指向的账号存在、`isExpired=false`、`isInvalid=false`。
4. 使用导入脚本时，先看到 `ready=true`；只有账号就绪后，才进行真实生成验证。

真实生成命令只作为验证模板，本文不声称本次更新已经执行并成功：

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
| `/health` 200 且 `browserConnected=true` 但生成 503 | 浏览器进程可能已启动，但 `/api/status` 的当前账号仍可能缺失、忙、过期或无效；按就绪条件复查。 |
| `/api/status` 返回 `currentAuthIndex=-1` 或账号 `isExpired=true` | 没有可用的当前账号，或导入的会话已过期；在 Web/VNC 控制台重新登录并重新导入。 |
| `/api/status` 重定向到 `/login` | 控制台登录会话无效；确认 `stack.env` 中的控制台密码与容器配置一致。 |
| 双击 `install.cmd` 后端口无响应 | 先看 `wsl -d Ubuntu-22.04 --exec docker ps` 是否有 `ai-proxy-kit-aistudio-1`。 |
| 改了 `stack.env` 但容器仍旧 key | `install` 会重建容器；只改文件不会自动生效。 |

## 未改动范围

- 不修改其他服务的镜像、端口或卷。
- 不自动登录 Google，不注入、不保存任何 Google 凭据。
- 不把 `runtime/` 下的任何密钥写入仓库或日志。
- 本文只更新 Windows 安装说明；没有记录或声称本次完成了真实生成。
