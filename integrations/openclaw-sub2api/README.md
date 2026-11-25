# OpenClaw Sub2API 分组模型同步

现有 Sub2API profile 安装此插件后，机器人使用自己的 Key 读取当前分组，
在每轮模型解析前选用分组的唯一实际上游模型。管理员换 Key 的分组或修改组模型后，
已有会话下一轮自动跟随。多个别名可指向同一目标；服务端负责应用白名单与模型映射。

## 安装

适用于 Linux、Python 3 和 Node.js 22 及以上版本。
Sub2API 需要提供 `GET /v1/sub2api/key-group`；对应后端补丁见仓库验收资料。
profile 的 `.env` 保留连接地址和机器人自己的 Key：

```dotenv
SUB2API_API_URL=http://sub2api-host:8080/v1
SUB2API_API_KEY=your-bot-key
```

从仓库运行安装器；`--profile` 默认为 `~/.openclaw`。

```bash
node integrations/openclaw-sub2api/install.mjs --profile /root/.openclaw
```

安装器读取当前 Sub2API profile、发现所属组模型，并用实际安装的 OpenClaw CLI
校验临时配置，随后备份并更新配置、环境文件及持久缓存。可用 `--node` 和
`--openclaw-entry` 指定网关使用的 Node 和 `openclaw/dist/index.js`。
插件路径必须在网关重启后仍可读取；可通过 `--plugin` 指定其安装目录。
首次安装后重启现有 OpenClaw 网关服务。
新版 OpenClaw 的第三方 `before_model_resolve` 钩子需要显式的
`hooks.allowConversationAccess` 权限。安装器通过实际 CLI 校验后配置它；
旧版不支持该字段时只省略该字段。插件不读取或发送会话正文，发现请求仅包含自己的 Key。

`SUB2API_MODEL`、`ACTIVE_MODEL_PROVIDER`、`ACTIVE_MODEL_ID` 的配置引用先迁移，
再从 `.env` 删除。`ACTIVE_MODEL_REASONING_EFFORT`、其他 Key、QQ、MCP 和独立设置保留。
安装结果包含私有备份路径，备份目录保存原配置、环境文件、缓存和安装记录。

## 分组模型发现（两级）

插件不要求 Sub2API 一定打过 key-group 补丁：

1. 首选 `GET {baseUrl}/sub2api/key-group`，读取 `schema_version=1` 的 `sub2api.key_group` 文档
   （含分组平台、别名、修订号），据此选择传输协议。
2. 该端点返回 404/405/501 时，回退到标准 `GET {baseUrl}/models`。Sub2API 会按 Key 的分组白名单过滤，
   因此回退结果**恰好只有一个**模型 id 时同样可信。回退沿用上次已知的分组 `platform`（无历史时按
`openai-completions`），避免一次 404 就把 anthropic/gemini/deepseek 部署改写成 openai 传输。

若 `/models` 返回 0 个或 ≥2 个 id，说明该 Key 未绑定单模型分组：插件把它当作**权威歧义**，
不写配置、也不覆盖每轮模型选择，`agents.defaults.model.primary` 继续生效。
这避免了把机器人钉在一个上游已失效的模型上（旧行为只保留旧模型）。

## 运行配置

安装器注册 `sub2api-auto` 插件并写入以下配置；密钥本身仍从环境读取。

```json
{
  "plugins": {
    "entries": {
      "sub2api-auto": {
        "enabled": true,
        "config": {
          "baseUrl": "http://sub2api-host:8080/v1",
          "keyEnv": "SUB2API_API_KEY",
          "configPath": "/root/.openclaw/openclaw.json",
          "statePath": "/root/.openclaw/state/sub2api-auto.json",
          "refreshIntervalMs": 15000,
          "timeoutMs": 4000
        }
      }
    }
  }
}
```

每轮请求等待正在进行的后台发现结束后，再发起一次新的元数据请求。
后台服务在启动时及每 15 秒同步持久配置；配置和缓存更新使用跨进程锁与恢复日志。
缓存绑定 Key 指纹和连接地址，不保存 Key 原文。所有缓存、恢复日志和配置备份仅属主可读。

空组、多个实际模型、接口失败及暂时断网会保留最后有效模型，并写出不含凭据的网关诊断。
本插件不生成 QQ 系统消息。显式选择其他 provider，或关闭插件后，其会话模型不被覆盖。
支持 `openai` / `deepseek`、`anthropic` 和 `gemini` 分组的对应协议。

## 验证

```bash
cd integrations/openclaw-sub2api
node --test test/*.test.mjs
```

测试覆盖下一轮切换、别名归一、失败保留、安装迁移、独立 Key/地址身份、
跨进程旧响应排序、配置/缓存故障恢复，以及锁进程意外退出。
生产验收还需要实际网关的同一会话切换和 Sub2API 上游使用记录。
