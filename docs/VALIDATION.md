# WSL 验收记录

环境：2026-11-20，Ubuntu 22.04 / WSL2，ARM64，Docker 29.3.0。
这些结果来自本机真实运行，不代替其他网络或新账号环境的验收。

| 项目 | 结果 |
| --- | --- |
| Antigravity | 监听 `0.0.0.0:18045`，容器 healthy，管理/API 鉴权正常，本地账号已导入 |
| WorkBuddy | 监听 `0.0.0.0:17863`，容器 healthy，3 个本人账号，59 个模型 |
| sub2api | 监听 `0.0.0.0:18080`，容器 healthy，PostgreSQL/Redis 独立命名卷 |
| AI Studio | 监听 `0.0.0.0:7860`，容器 healthy，网页 HTTP 200；无密钥 401，鉴权后列出 31 个模型 |
| WorkBuddy 普通生成 | `cn:hy4flash-free` 返回 `PROXY_KIT_OK` |
| WorkBuddy 流式生成 | 内容匹配并收到 `[DONE]` |
| sub2api → WorkBuddy 普通生成 | 独立分组、API Key 转发成功，返回 `PROXY_KIT_OK` |
| sub2api → WorkBuddy 流式生成 | 独立调用 Key 的 SSE 内容匹配，收到 `[DONE]`；重复执行没有新建重复账号 |
| sub2api 启停后持久化 | 使用 stop/start 停止并重启数据库、Redis 和服务后，原账号、分组、调用 Key 仍有效，流式转发复测通过 |
| Antigravity 上游生成 | 本轮 Google 返回 `403 UNSUPPORTED_LOCATION`，直接请求也复现；不判为通过 |
| AI Studio 生成 | 尚未完成本人 Google 浏览器登录，`browserConnected=false`；未声称生成通过 |
| 配置回归 | 保留已有凭据、保留账号和未知配置、冲突端口拒绝、env 字面值/重复项检查通过 |
| Windows 入口 | PowerShell 5.1 原生函数检查全部通过，统一 WSL `status` 入口真实调用成功 |

本机已有旧实例，因此统一测试栈使用备用端口；发布脚本的默认端口仍是
`8045`、`7863`、`8080`、`7860`，所有网关固定发布到 `0.0.0.0`。

## 修正与明确限制

- WorkBuddy v1.13.0 出站 Transport 未配置 `ProxyFromEnvironment`，在本机 Fake-IP DNS
  环境里直接拨号超时。针对固定源提交的最小补丁已编译并真实验证；构建回执保存于本机。
- 子网关没有生成额度、输出预算不够、账号被限流时，`check --model` 会失败；不会以
  容器 healthy 或只有思考内容的空回复判断成功。
- sub2api 测试账号初始余额为零。本轮在独立 WSL 实例中通过管理接口设置了 1 个测试余额单位，
  用于实际转发验收，不涉及支付；脚本不会自动给其他实例充值。
- sub2api 的首次管理员确认由用户明确授权后提交；发布脚本默认要求本人先完成，
  仅在显式传入 `--acknowledge-compliance` 时自动提交。
- 旧 Windows 实例初始密码已失效。本轮按用户要求在 WSL 独立实例完成配置，没有改写
  Windows 管理员密码或业务数据。
- Cookie、OAuth Token、API Key、数据库密码、登录回执与二进制均在 Git 忽略路径下。

复测：

```bash
bash status.sh
bash check.sh workbuddy --model cn:hy4flash-free --stream
bash connect.sh --provider workbuddy --test --model cn:hy4flash-free --stream
```
