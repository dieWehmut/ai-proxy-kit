# AI Studio Playground 迁移验证（2026-11-25）

> 本记录中的 7870 是本次 LXC 独立部署的**宿主机端口**。仓库 Compose 服务名是
> `aistudio`，容器端口是 `2048`；LXC 现网通过 `AISTUDIO_PORT=7870` 发布为
> `0.0.0.0:7870 -> 容器 2048`。同栈 Sub2API 使用 `http://aistudio:2048`；只有跨栈访问
> 才使用宿主机地址 `http://172.18.0.1:7870`。

## 结论

LXC158 的 AI Studio 生成上游已从 7860 的 Build 通道迁移到 7870 的
Playground(AIStudio2API) 通道。7860 的旧 Build 反代在 Google 会话仍然有效时仍被上游
拒绝生成，错误不是账号、网络或本地配置造成的。Playground 通道使用同一份会话完成了导入，
`gemini-4-hydrogen` 与 `gemini-3.8-flash` 的非流式生成均返回 `PROXY_KIT_OK`。Sub2API
上游应改指 `http://172.18.0.1:7870`。

## 1. 7860 Build 通道仍拒绝生成

本次验证对象是 `ai-proxy-kit-aistudio-1`，镜像为
`ibuhub/aistudio-to-api:v1.4.1`。发起请求前，VNC 登录已经完成：

- `auth-1` 仍然有效、未过期。
- AI Studio 页面已经点过 **Continue to the app**。
- 因此本次测试不处于未登录或登录失效状态。

在该状态下请求 `/v1/chat/completions`，仍得到 HTTP 400：

| 请求形式 | 错误消息 |
| --- | --- |
| 非流式 | `PROXY_GENERATION_DISABLED: generateContent` |
| `stream=true` | `PROXY_GENERATION_DISABLED: streamGenerateContent` |

流式与非流式请求分别对应 `streamGenerateContent` 和 `generateContent`，两者都被同一层
生成能力禁用拦截。该结果说明继续排查 VNC 登录、账号槽位、网络或本地 key 配置都不会恢复
7860 的生成能力。

## 2. 上游关闭 Build 渠道

上游项目 [iBUHub/AIStudioToAPI issue #302](https://github.com/iBUHub/AIStudioToAPI/issues/302)
已公告：Google 于 **2026-10-30** 关闭 Build 渠道。由此可以确定，7860 的 400 是上游能力
下线，不是账号、网络或配置故障。

## 3. Playground 通道导入与生成

LXC158 改用 `ghcr.io/mag1cfall/aistudio2api` 的 Playground 通道。使用
`aistudio2api setup --storage-state` 导入与 7860 测试相同的会话，导入成功。

导入后通过受保护的管理端读取 `/api/status`，观察到：

```text
status: RUNNING
accounts.ready: 1
```

随后使用 Playground 的 `/v1` 接口做非流式生成，结果如下：

| 模型 | 非流式结果 |
| --- | --- |
| `gemini-4-hydrogen` | `PROXY_KIT_OK` |
| `gemini-3.8-flash` | `PROXY_KIT_OK` |

这说明同一份 Google 会话在 Playground 通道可用，7860 的失败点位于 Build 生成能力禁用，
而不是会话本身。

## 4. 7870 监听、鉴权与 Sub2API 容器侧验证

7870 原先只监听 `127.0.0.1:7870`。本次将监听地址改为可配置的 `LISTEN_ADDR`，并设置为
`0.0.0.0:7870`，使 Compose 网络中的 Sub2API 容器可以访问宿主机端口。

| 项目 | 当前配置 |
| --- | --- |
| 监听地址 | `LISTEN_ADDR=0.0.0.0:7870` |
| 管理接口 `/api` | `ADMIN_AUTH_ENABLED=true`，需要管理员认证 |
| 生成接口 `/v1` | 由 `PROXY_API_KEY` 保护 |
| Sub2API 容器访问地址 | `http://172.18.0.1:7870` |

`172.18.0.1` 是本次环境中从 `sub2api` 容器访问宿主机的地址。不要改写成 Sub2API 容器
自身的 `127.0.0.1`。

在 LXC158 上确认只监听目标地址，不读取或打印密钥：

```bash
ss -lntp | grep ':7870'
```

预期监听地址为 `0.0.0.0:7870`，不能停留在 `127.0.0.1:7870`。

从未携带管理员认证的 `/api` 请求不应得到管理数据；未携带 `PROXY_API_KEY` 的 `/v1`
请求不应进入生成逻辑。验证时通过管理端登录或受控的本机管理通道读取状态，不要把
管理员凭据写入命令历史、文档或日志。

从 `sub2api` 容器验证模型列表。`PROXY_API_KEY` 应由受控环境或秘密管理注入，命令只
引用变量，不回显值：

```bash
docker exec -e PROXY_API_KEY="$PROXY_API_KEY" <sub2api-container> sh -lc \
  'curl -sS -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Bearer $PROXY_API_KEY" \
    http://172.18.0.1:7870/v1/models'
```

本次实测返回 HTTP 200。生成验证使用当前模型列表中的 ID，非流式请求返回
`PROXY_KIT_OK`：

```bash
for model in gemini-4-hydrogen gemini-3.8-flash; do
  curl -sS \
    -H "Authorization: Bearer $PROXY_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply OK\"}],\"stream\":false}" \
    http://172.18.0.1:7870/v1/chat/completions
  echo
done
```

这两个模型在本次实测中均返回 `PROXY_KIT_OK`。上述命令不输出 `PROXY_API_KEY`。

## 5. Sub2API 切换与后续验收

Sub2API 的 AI Studio 上游账号应改为：

```text
Base URL: http://172.18.0.1:7870
API Key: 使用与 PROXY_API_KEY 相同的值
```

不要继续使用 `http://aistudio:7860`。从 `sub2api` 容器内直连 7870 已验证网络与 API
可用；这不等于 Sub2API 账号路由已经完成验收。修改账号后仍需在 Sub2API 管理端确认保存，
再选择 `/v1/models` 当前返回的模型做一次非流式生成，只有结果包含 `PROXY_KIT_OK` 才能
把路由记为通过。

## 6. 最终处置

| 通道 | 用途 |
| --- | --- |
| 7860 Build，`ibuhub/aistudio-to-api:v1.4.1` | 仅保留 UI/管理，不再作为生成上游 |
| 7870 Playground，`ghcr.io/mag1cfall/aistudio2api` | 当前生成上游 |
| Sub2API | 指向 `http://172.18.0.1:7870` |

本记录只包含本次实测的状态、错误消息、模型 ID 和 HTTP 结果，不包含邮箱、Cookie、API key、
管理员凭据或其他秘密。

## 7. 与仓库 Compose 的端口对应

上面的 7870 结果来自 LXC 上的 Playground 部署。迁移到仓库 Compose 后，服务名统一为
`aistudio`，`compose.yml` 中的容器监听固定为 `0.0.0.0:2048`，宿主端口为
`${AISTUDIO_PORT:-2048}`。LXC158 保留现有入口时设置：

```text
AISTUDIO_PORT=7870
```

因此：

| 访问方 | 地址 |
| --- | --- |
| LXC 宿主机或本机客户端 | `http://127.0.0.1:7870` |
| 同一 Compose 网络中的 Sub2API | `http://aistudio:2048` |
| 不同网络中的容器，经宿主机网关 | `http://172.18.0.1:7870`（按实际网关核对） |

`7870` 只属于宿主机发布端口差异；容器内部仍是 `2048`，不是另开了一个 AI Studio 服务。
Sub2API 与 `aistudio` 同栈时不要填宿主机 IP，也不要在容器内填 `127.0.0.1`。修改端口后
必须重建容器，并以 `docker compose --profile aistudio port aistudio 2048` 和
`/api/status` 重新验收。
