# AIStudio2API Playground 反代

本文说明仓库的 Playground 版 AI Studio 反向代理、Windows 会话导入入口和验收标准。
Build 通道已停用；当前唯一可用的生成通道是 AIStudio2API 的 Playground。7860 的旧
Build 反代不能继续作为生成上游，相关错误形状见
[AI Studio Playground 迁移验证](validation/aistudio-playground-migration-20261125.md)。

本文是部署与验收说明，不声称本机已经完成部署或实测。所有密钥、账号和会话都必须由本机
秘密管理或交互输入提供，不能写进仓库、文档、聊天记录或命令输出。

## 本机部署

前置条件是 Windows 10/11、WSL2、可用的 Linux 发行版，以及该发行版内可由当前用户访问的
Docker。以下命令在仓库根目录对应的 WSL shell 中执行：

```bash
repo=/path/to/ai-proxy-kit
mkdir -p "$repo/runtime/aistudio2api"

docker run -d \
  --name aistudio2api-local \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 2048:2048 \
  -e PROXY_API_KEY="$PROXY_API_KEY" \
  -e ADMIN_AUTH_ENABLED=true \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  -e AUTO_START=true \
  -e WAA_BACKEND=spidermonkey \
  -e UPSTREAM_CHANNELS=playground \
  -v "$repo/runtime/aistudio2api:/data" \
  ghcr.io/mag1cfall/aistudio2api:latest
```

`PROXY_API_KEY` 和 `ADMIN_PASSWORD` 必须先在当前受控 shell 中设置，命令只引用变量，不展开
到文档。`2048` 是生成接口和管理界面的端口；`AUTO_START=true` 让生成服务随管理进程启动；
`WAA_BACKEND=spidermonkey` 使用默认的进程内后端。`ADMIN_AUTH_ENABLED=true` 保护 `/api`，
管理用户名由 `ADMIN_USERNAME` 指定，默认是 `admin`。

容器的 `/data` 必须绑定到仓库忽略提交的 `runtime/aistudio2api`。这个目录同时保存导入的
会话和运行状态，不能提交到 Git。如果使用仓库的 Compose 服务，其 `/data` 挂载位于
`runtime/aistudio2api/data`；此时导入时把 `-DataDir` 指向该目录。

启动后先确认容器和健康接口可达。`GET /health` 返回 `{"status":"ok"}` 只能证明管理进程
还活着，不能证明账户可以生成。

## 导入账号

从已经登录 Google 的浏览器导出 Playwright storage-state JSON。Windows 一键入口会先解析
注册表中的 Downloads 目录，再通过 `wslpath` 转换路径，把会话复制为仓库内的
`runtime/aistudio2api/session.json`，最后在容器内执行：

```text
aistudio2api setup --storage-state /data/session.json
```

默认容器名是 `aistudio2api-local`，默认源文件是
`%USERPROFILE%\Downloads\ai-proxy-kit-google-session.json`。双击
`import-aistudio2api-session.cmd`，或从 PowerShell 运行：

```powershell
.\import-aistudio2api-session.cmd
```

需要覆盖参数时：

```powershell
.\import-aistudio2api-session.cmd `
  -Source "C:\path\to\google-session.json" `
  -Distribution "Ubuntu-22.04" `
  -DataDir "D:\secure\aistudio2api" `
  -Container "aistudio2api-local"
```

`-DataDir` 必须对应容器内的 `/data` 挂载；脚本始终把文件命名为 `session.json` 并通过
`--storage-state /data/session.json` 导入。入口不会打印 cookie 或会话正文。导入完成后应删除
Windows 下载副本和 `runtime/aistudio2api/session.json`，并确认 `runtime/` 仍在
`.gitignore` 中。

## 就绪判据

`/api/status` 是账号就绪的管理接口。开启 `ADMIN_AUTH_ENABLED=true` 后，应先在浏览器中的
`/login` 页面登录，或通过 `POST /api/auth/login` 建立管理员 Cookie 会话；不要把密码放在
URL、日志或查询串中。只读取非秘密字段即可：

```bash
ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
COOKIE_JAR="$(mktemp)"
python3 -c 'import json,os; print(json.dumps({"username":os.environ["ADMIN_USERNAME"],"password":os.environ["ADMIN_PASSWORD"],"remember":False}))' |
  curl -fsS -c "$COOKIE_JAR" -H 'Content-Type: application/json' --data-binary @- \
    http://127.0.0.1:2048/api/auth/login >/dev/null
curl -fsS -b "$COOKIE_JAR" http://127.0.0.1:2048/api/status |
  python3 -c 'import json,sys; s=json.load(sys.stdin); print("state=%s running=%s ready=%s accounts.ready=%s accounts.busy=%s" % (s.get("state"), s.get("running"), s.get("ready"), s.get("accounts", {}).get("ready"), s.get("accounts", {}).get("busy")))'
rm -f "$COOKIE_JAR"
```

合格条件是：

- `state` 为 `RUNNING`；
- `running` 和顶层 `ready` 都为 `true`；
- 空闲验收时 `accounts.ready >= 1`；账户正在处理请求时，也可由
  `accounts.ready + accounts.busy >= 1` 说明至少有一个可用账户。

`/health` 成功、容器 `running`、或只看到 `accounts.total=1` 都不能代替以上判据。

## 真实生成验收

先用 `PROXY_API_KEY` 读取当前模型列表，再选其中一个模型发送非流式 Chat Completions。
下面的命令只输出结果摘要，不回显 key，也不把完整上游响应当作日志：

```bash
model=$(curl -fsS \
  -H "Authorization: Bearer $PROXY_API_KEY" \
  http://127.0.0.1:2048/v1/models |
  python3 -c 'import json,sys; d=json.load(sys.stdin); m=d.get("data") or d.get("models"); assert m; print(m[0]["id"])')
printf 'ACCEPTANCE_MODEL=%s\n' "$model"

curl -fsS \
  -H "Authorization: Bearer $PROXY_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply exactly PROXY_KIT_OK\"}],\"stream\":false}" \
  http://127.0.0.1:2048/v1/chat/completions |
  python3 -c 'import json,sys; d=json.load(sys.stdin); text=d["choices"][0]["message"]["content"]; assert "PROXY_KIT_OK" in text, text; print("generation=PASS")'
```

最后一行必须是 `generation=PASS`。模型 ID 必须来自本次 `/v1/models` 的返回，不能沿用历史
文档或旧容器里的名称。匿名 `/v1` 请求应被拒绝；带错误 key 的请求不应进入生成逻辑。

## Sub2API 上游

Sub2API 的 AI Studio 上游地址应设为：

```text
http://host.docker.internal:2048
```

API Key 使用与 AIStudio2API 的 `PROXY_API_KEY` 相同的值。Docker Desktop 自带
`host.docker.internal`；在 WSL 内运行 Docker Engine 时，像上面的 `docker run` 一样为
Sub2API 容器添加 `--add-host=host.docker.internal:host-gateway`，或在 Compose 中设置
`extra_hosts: ["host.docker.internal:host-gateway"]`。不要填写容器自身的 `127.0.0.1`。

保存 Sub2API 账号后，还要通过 Sub2API 的 provider key 和当前 `/v1/models` 中的模型完成
一次真实生成，结果包含 `PROXY_KIT_OK` 才表示路由验收通过。只是从 Sub2API 容器能访问
2048 端口，不等于账号路由已经可用。
