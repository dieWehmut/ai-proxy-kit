# AI Proxy Kit

统一管理 **Antigravity Manager、WorkBuddy2API、Sub2API**，另提供选定的
**AIStudioToAPI** 可选入口。所有对外网关明确监听并发布 **`0.0.0.0`**。

支持 Linux AMD64 / ARM64、WSL2，以及 Windows 双击调用 WSL。
保留原 `sub2api-windows-installer` 的 Git 历史和 Windows 原生安装器。

| 服务 | 默认端口 | 本机页面 | 镜像版本 |
| --- | --- | --- | --- |
| Antigravity | 8045 | `http://localhost:8045` | `lbjlaq/antigravity-manager:v5.0.3` |
| WorkBuddy | 7863 | `http://localhost:7863/panel/` | `ghcr.io/linguo2625469/workbuddy2api-panel:1.13.0` |
| sub2api | 8080 | `http://localhost:8080` | `weishaw/sub2api:0.2.23` |
| AI Studio（可选） | 7860 | `http://localhost:7860` | `ibuhub/aistudio-to-api:v1.4.1` |

`0.0.0.0` 是监听地址。客户端使用 `localhost` 或主机 IP。
PostgreSQL / Redis 仅供 Compose 网络中的 sub2api 使用，不发布数据库端口。

## Linux / WSL 一键安装

```bash
git clone https://github.com/dieWehmut/ai-proxy-kit.git
cd ai-proxy-kit
bash install.sh all
```

`all` 包含前三项服务。首次运行生成独立随机密钥，保存在仅当前用户可读的
`runtime/stack.env`。账号、程序配置和命名卷均持久化；重跑不重置密钥。
Python 3 / curl 缺失时，Linux 启动器通过 apt/dnf 安装。
需要安装 Docker Engine 时，显式使用官方 Linux 安装脚本：

```bash
bash install.sh all --install-docker
```

已有 Docker 必须能使用 `docker compose`。WSL 可复用已有 Linux Docker 或
Docker Desktop WSL 集成。脚本不更改虚拟化设置或重启 Windows。
Docker daemon 无法访问镜像源时，会尝试用经过校验的 `crane` 经当前 shell 代理拉取镜像。
日常启动复用本机镜像，不自动升级。

```bash
# 单项安装，AI Studio 为可选服务。
bash install.sh antigravity
bash install.sh workbuddy
bash install.sh sub2api
bash install.sh aistudio

# 一键启动、停止、状态、检查。
bash start.sh all
bash stop.sh all
bash status.sh
bash check.sh all
bash start.sh antigravity --skip-pull

# 指定模型会验证真实生成，而不只检查容器。
bash check.sh workbuddy --model cn:hy4flash-free
bash check.sh workbuddy --model cn:hy4flash-free --stream
bash check.sh antigravity --model gemini-3.8-flash-low
```

## Windows 一键入口

双击 `install.cmd`、`start.cmd`、`stop.cmd`、`status.cmd`。
统一入口调用 WSL 中同一套 Linux 脚本；仓库可保存在 Windows 或 WSL UNC 目录。
AI Studio 安装与验收见 [Windows 本机安装 AI Studio 反代](docs/WINDOWS_AISTUDIO_INSTALL.md)；
`open-aistudio.cmd` 会从 `stack.env` 重新生成登录页并直接用默认浏览器打开。

```powershell
.\proxykit.ps1 -Action install -Service all -Distribution Ubuntu-22.04
.\proxykit.ps1 -Action start -Service antigravity -Distribution Ubuntu-22.04
.\proxykit.ps1 -Action status
.\proxykit.ps1 -Action access   # regenerate runtime/LOCAL_ACCESS.txt and the AI Studio login page
```

也可以下载 [脚本压缩包](https://github.com/dieWehmut/ai-proxy-kit/archive/refs/heads/main.zip)，
解压后运行上述入口。制作同时包含 Linux / Windows 脚本的发布 ZIP：

```powershell
.\tools\Build-Release.ps1 -Version 1.1.0
```

原有 Windows **原生** Sub2API 安装器仍可用：`install-sub2api.cmd` / `start-sub2api.cmd`。
详见 [原生安装器说明](docs/SUB2API_WINDOWS.md)；它与统一栈属于不同实例，应使用不同端口。

## 首次账号与出站代理

- Antigravity：用 `ANTIGRAVITY_WEB_PASSWORD` 登录并添加账号。启动器自动启用反代和自动启动；新账号先刷新配额。
- WorkBuddy：进入 `/panel/`，使用 `WORKBUDDY_API_KEY` 管理本人账号。默认关闭积分任务排程。
- sub2api：用 `SUB2API_EMAIL` / `SUB2API_PASSWORD` 登录。首次在页面确认上游管理员承诺；标准计费模式需要正余额。
- AI Studio：控制台密码为 `AISTUDIO_WEB_PASSWORD`，API Key 为 `AISTUDIO_API_KEY`；通过控制台 VNC 完成本人 Google 登录。

本机浏览器已经登录 Google 时，可以直接[导出并复用现有会话](docs/AISTUDIO_LOCAL_LOGIN.md)，
导入脚本会通知现有 AI Studio 重载。

已有账号可显式接入；Windows 目录在 WSL 下使用 `/mnt/c/`、`/mnt/d/` 路径：

```bash
bash install.sh all \
  --import-antigravity /path/to/Antigravity-Manager/docker/.env \
  --antigravity-data /path/to/idle-antigravity-data \
  --import-workbuddy /path/to/workbuddy2api \
  --upstream-proxy http://host.docker.internal:7890 \
  --workbuddy-env-proxy
```

`--antigravity-data` 复用指定目录；不要让两个实例同时写同一目录。
WorkBuddy 导入只复制缺失的 `auths/*.json`，不改源文件。
容器里的 `127.0.0.1` 是容器本身，出站代理应使用可达的宿主机地址。
`host.docker.internal` 已配置；WSL 的 Windows 代理也可用 Windows 网关 IP。

WorkBuddy v1.13.0 的自定义 Transport 未读取代理环境变量。
`--workbuddy-env-proxy` 下载固定提交并验证 SHA-256，仅补入
`http.ProxyFromEnvironment` 后编译覆盖层。复用 Go >=1.22.5；缺失时将官方校验过的
Go 工具链放进 `runtime/toolchain/`，不修改系统 Go。二进制与原许可证留在本机。

## 添加到 sub2api

```bash
# 首次先在 sub2api 页面完成管理员确认。
bash connect.sh
# 创建/复用该上游的调用 Key，再验证真实转发。
bash connect.sh --provider workbuddy --test --model cn:hy4flash-free
bash connect.sh --provider workbuddy --test --model cn:hy4flash-free --stream
bash connect.sh --provider antigravity --test --model gemini-3.8-flash-low
# AI Studio 安装后可先接入；完成 Google 登录后再执行 --test。
bash connect.sh --provider aistudio
bash connect.sh --provider aistudio --test
# 明确同意上游文档后，可非交互提交确认。
bash connect.sh --acknowledge-compliance
```

默认为 Antigravity、WorkBuddy 分别创建 **OpenAI / API Key** 账号和独立分组；
AI Studio 用 `--provider aistudio` 单独接入。固定 Chat Completions
协议，Compose 内通过服务名连接。重复运行复用原账号、分组和调用 Key。
读回 ID 和调用 Key 保存在 `runtime/sub2api-connections.json`，不输出凭据。

接入其他 sub2api 可设置 `SUB2API_BASE_URL` 和 `SUB2API_JWT` / `SUB2API_ADMIN_API_KEY`；
或把 `SUB2API_EMAIL` / `SUB2API_PASSWORD` 写进被忽略的 `.env.sub2api.local`。
创建用户调用 Key 做 `--test` 需要 JWT 或邮箱/密码。
上游不在同一个 Compose 网络时，单项指定 `--provider antigravity --upstream-url http://实际IP:8045`。

## 端口、数据和验证

```bash
# 本机已有实例时换一组端口；仍全部发布到 0.0.0.0。
bash install.sh all --antigravity-port 18045 --workbuddy-port 17863 --sub2api-port 18080
python3 -m unittest discover -s tests -p 'test_*.py'
bash -n *.sh
```

端口冲突时不会停止其他程序。停止命令不删除数据。迁移时同时备份 `runtime/` 和
sub2api 的 Docker 命名卷；只复制脚本不会迁移账号。
WSL2 NAT 下，从局域网经 Windows IP 访问仍取决于 Windows 转发和防火墙。

GitHub Actions 检查 Linux 配置、凭据保留、网关绑定地址，以及 Windows PowerShell 5.1 / 7。
真实结果见 [WSL 验收记录](docs/VALIDATION.md)。AI Studio 的选择依据见 [方案比较](docs/AISTUDIO_COMPARISON.md)。

## 来源

本仓库脚本沿用 [MIT](LICENSE)。上游程序分别来自
[Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager)、
[WorkBuddy2API Panel](https://github.com/linguo2625469/workbuddy2api-panel)、
[Sub2API](https://github.com/Wei-Shaw/sub2api)、[AIStudioToAPI](https://github.com/iBUHub/AIStudioToAPI)，
各自遵循其许可证。其中 AIStudioToAPI 使用 CC BY-NC 4.0；部署入口不改变上游授权。
