# AI Studio 方案比较

调研日期：2026-11-20。选择 **iBUHub/AIStudioToAPI**，以可选 profile 纳入。

| 项目 | CJackHwang/AIstudioProxyAPI | iBUHub/AIStudioToAPI |
| --- | --- | --- |
| 网页来源 | AI Studio Chat | AI Studio Build App |
| 实现 | Python / FastAPI / Playwright / Camoufox | Node.js / 浏览器自动化 / Camoufox |
| 公布的 API | OpenAI Chat Completions / models | OpenAI / Gemini / Anthropic |
| 工具调用 | auto/native/emulated，带回退 | 三种协议的工具调用适配 |
| 首次登录 | 文档建议 debug 模式保存 auth，再 headless | Docker 内通过 Web/VNC 登录，无需预先导出 auth |
| Docker | 配置浏览器和已有认证，自建 Dockerfile | GHCR / Docker Hub；linux/amd64、linux/arm64 |
| 当前提交日期 | 2026-04-22 | 2026-10-24（仓库 pushed_at：2026-10-28） |
| 许可证 | AGPL-3.0 | CC BY-NC 4.0，非商业授权 |

本任务在 ARM64 WSL/Linux 中集中运行，后者提供匹配架构的镜像、Web 首次授权与多协议
入口，集成工作更少。因此选择它。表中能力取自文档和源码，不代表此次已用 Google 登录
完成所有协议的生成测试。

纳入版本为 `ibuhub/aistudio-to-api:v1.4.1`，已核对其与当时 latest 的多架构 manifest
digest 一致：`sha256:223e4a66af259e2617847947639b7c00224773207017a20b34b139f6dbf4d9d0`。
普通启动复用本机已缓存版本，不自动跟随 latest。

首次 Google 登录必须由本人完成。认证过期、网页变更或验证挑战仍可能中断服务；
Camoufox 还需要额外内存。控制台和 API 分别设置随机密码，认证目录只在本机持久化。

原始依据：

- [CJackHwang README](https://github.com/CJackHwang/AIstudioProxyAPI/blob/main/README.md)
- [CJackHwang Docker 文档](https://github.com/CJackHwang/AIstudioProxyAPI/blob/main/docker/README-Docker.md)
- [CJackHwang LICENSE](https://github.com/CJackHwang/AIstudioProxyAPI/blob/main/LICENSE)
- [iBUHub README](https://github.com/iBUHub/AIStudioToAPI/blob/main/README.md)
- [iBUHub Dockerfile](https://github.com/iBUHub/AIStudioToAPI/blob/main/Dockerfile)
- [iBUHub 发布工作流](https://github.com/iBUHub/AIStudioToAPI/blob/main/.github/workflows/docker.yml)
- [iBUHub 配置](https://github.com/iBUHub/AIStudioToAPI/blob/main/.env.example)
- [iBUHub LICENSE](https://github.com/iBUHub/AIStudioToAPI/blob/main/LICENSE)
