# Sub2API Windows 一键安装

下载、解压，双击 `install-sub2api.cmd`。脚本会安装 Windows 原生 Sub2API、启动 PostgreSQL 和 Redis、自动创建管理员，完成后直接打开登录页，**无需经过 Sub2API 安装向导**。

新安装默认关闭 Cloudflare Turnstile 人机验证。每次新安装生成独立的数据库密码、管理员密码、JWT 密钥和 TOTP 加密密钥，不使用共享默认密码。

## 使用条件

- Windows 10/11 x64，或支持 x64 模拟的 Windows 11 ARM64；使用系统自带的 Windows PowerShell 5.1。
- 可用的 Docker Linux 引擎和 Docker Compose v2：推荐 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，也支持已配置 Docker 的 WSL 发行版。
- 能访问 GitHub 发布文件和容器镜像仓库。

脚本会检测 Docker Desktop 与 WSL；已安装的 Docker Desktop 未启动时会尝试启动。**Docker 本身需要先安装好**；脚本不会自动安装 WSL、变更 Windows 虚拟化设置或重启系统。无需安装 Go、Node.js，也无需手工配置数据库。

## 一键安装

1. 从 [Releases](https://github.com/dieWehmut/sub2api-windows-installer/releases/latest) 下载脚本 ZIP，解压到任意目录。也可使用 GitHub 的 **Code → Download ZIP**。
2. 双击 `install-sub2api.cmd`。
3. 等待安装完成，在自动打开的登录页登录。初始管理员邮箱和随机密码保存在安装目录中的 `LOGIN.txt`。

默认安装目录为 `%LOCALAPPDATA%\Sub2API`，本机地址为 <http://127.0.0.1:8080/login>。默认管理员邮箱是 `admin@sub2api.local`；可通过下面的参数自定义。脚本也会创建桌面启动快捷方式。

首次运行下载官方最新稳定版并验证 SHA-256，下载 Windows 所需的时区数据，然后通过 `AUTO_SETUP=true` 完成数据库迁移和管理员创建。只有健康接口、登录页面及安装状态检查通过，安装器才报告成功；首次安装还会实际验证管理员无验证码登录。

## 自定义目录、端口和账号

在解压目录的 PowerShell 中运行：

```powershell
.\install-sub2api.ps1 `
  -InstallDir "D:\Apps\Sub2API" `
  -Port 8080 `
  -DatabasePort 5432 `
  -RedisPort 6379 `
  -AdminEmail "you@example.com"
```

从命令提示符运行也可以：

```bat
install-sub2api.cmd -InstallDir "D:\Apps\Sub2API" -Port 18080 -DatabasePort 15432 -RedisPort 16379
```

| 参数 | 作用 |
| --- | --- |
| `-InstallDir` | 安装目录，支持空格；首次安装必须为空目录 |
| `-Port` | Sub2API 网页端口，默认 `8080` |
| `-DatabasePort` / `-RedisPort` | 仅绑定本机的数据库端口，默认 `5432` / `6379` |
| `-AdminEmail` | 首次创建的管理员邮箱 |
| `-Version v0.2.23` | 首次安装指定官方版本；默认 `latest` |
| `-DockerDistribution Ubuntu-22.04` | 明确选择 WSL 发行版；默认自动检测 |
| `-Architecture amd64` / `arm64` | 明确选择上游提供的 Windows 架构；默认自动检测 |
| `-NoBrowser` / `-NoShortcut` | 禁止打开浏览器 / 创建桌面快捷方式 |

Windows ARM64 在官方仅提供 amd64 包时使用系统的 x64 模拟运行。明确指定不存在的架构会报错。

## 以后如何启动

双击安装目录中的 `start-sub2api.cmd` 或桌面快捷方式。服务在后台运行，关闭安装窗口不会停止服务。双击 `stop-sub2api.cmd` 停止 Sub2API；数据库、Redis 和持久化数据保留。

服务监听 `0.0.0.0`，启动脚本显示本机登录地址和当前局域网 IPv4 地址。局域网连接仍由 Windows 防火墙规则决定；浏览器访问时使用实际 IP，而不是 `0.0.0.0`。

临时切换网页端口前，先停止实例，再运行：

```bat
start-sub2api.cmd -Port 18080
```

这个参数只影响本次启动，不修改保存的默认端口。

## 重复安装与数据

再次运行安装器会复用原来的凭据、程序版本和数据卷，不重置管理员，不启动第二个实例，也不自动升级已安装的版本。安装中断后可以在同一目录重跑。

不同安装目录使用独立的 Docker 项目和命名卷，并保存首次选择的 Docker 引擎或 WSL 发行版，后续启动继续使用同一套数据服务。如果原目录的 `.env` 丢失，但数据卷仍存在，脚本会要求恢复原配置，避免生成与已有数据库不匹配的密码。已有其他方式安装的目录不会被覆盖。

修改管理员密码后，`LOGIN.txt` 中的初始密码不会同步改变；以后请使用自己设置的新密码。

以下文件属于本机运行数据，不应上传到 GitHub：

- `.env`、`config.yaml`、`LOGIN.txt`：含本实例凭据。
- `.installed`：Sub2API 自动初始化完成后生成的标记。
- `logs/`、`downloads/`、`sub2api.exe`、`zoneinfo.zip`：运行日志和下载文件。

本仓库的 `.gitignore` 已排除这些文件；公开发布包只包含脚本、文档与 Compose 模板。

## 故障排查

- **Docker 不可用**：打开 Docker Desktop 并切到 Linux containers，或确认 WSL 内的 `docker info` 和 `docker compose version` 可用。
- **端口占用**：用安装参数选择另一组互不相同的端口；脚本不会停止占用端口的其他程序。
- **下载失败**：确认网络可访问 GitHub 后重跑。GitHub API 限流时，脚本自动改用官方发布页面和校验和文件，不要求登录 GitHub。也可在当前 PowerShell 进程中设置自己的 `GITHUB_TOKEN`，该值不会写入安装配置。
- **启动失败**：查看安装目录的 `logs\launcher-error.log`、`logs\sub2api-error.log` 和 `logs\sub2api.log`。

## 验证脚本

不需要 Docker 的语法和函数检查：

```powershell
.\tests\Test-Functions.ps1
```

在可用的 Windows Docker 环境进行真实安装检查：

```powershell
.\tests\Test-Installation.ps1 -Version v0.2.23
```

集成检查使用带空格的临时目录和独立端口，验证自动初始化、无验证码管理员登录、重复安装，以及重建容器后管理员和数据库测试记录保留。结束后只清理这个测试实例；`-KeepInstallation` 可保留它供检查。GitHub Actions 在 Windows PowerShell 5.1 和 PowerShell 7 上运行语法、凭据隔离、权限、校验和及参数引用检查。

## 许可证与来源

安装和启动脚本使用 [MIT License](LICENSE)。Sub2API 程序从 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) 官方 Releases 下载，遵循其自身许可证，安装目录中保留 `SUB2API-LICENSE`。Docker 镜像及 [Go 时区数据](https://github.com/golang/go/tree/go1.24.0/lib/time) 遵循各自的许可证。
