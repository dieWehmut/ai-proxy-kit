# OpenClaw × Sub2API 分组模型自动同步（运维说明）

适用对象：LXC 152、153 上运行 OpenClaw QQ 机器人的运维者（两个实例、各用独立 Sub2API Key）。
关联代码：`integrations/openclaw-sub2api/`（`index.mjs`、`group-sync.mjs`）。
规格来源：`docs/OPENCLAW_SUB2API_SYNC_SPEC.md`。
适用版本：`@ai-proxy-kit/openclaw-sub2api` ≥ 0.1.1（引入 `/v1/models` 回退与歧义抑制）。

> 安全红线：本文档、网关日志、诊断文件、备份目录都**不得**出现 Key、token、cookie 原文。
> 状态文件只保存 Key 的 SHA-256 指纹，不保存 Key 本身。排错命令只输出 HTTP 状态码或非敏感元数据。

---

## 1. 目标

机器人所用模型的**唯一事实来源**是 Sub2API 中该 Key 当前所属的分组。

- 管理员把 Key 换到另一个分组，或修改该分组唯一的模型配置后，运行中的 OpenClaw 在**下一次模型解析**时自动跟随。
- 不需要改 `.env`、不需要清理 QQ 聊天历史、不需要重启网关。
- `.env` 只保留连接地址和机器人自己的 Key（`SUB2API_API_URL`、`SUB2API_API_KEY`）；不再手写模型或 provider 选择变量。

## 2. 两级发现顺序

每轮解析先尝试权威端点，失败再回退到标准端点。

| 顺序 | 端点 | 前提 | 采纳条件 |
| --- | --- | --- | --- |
| 首选 | `GET {baseUrl}/sub2api/key-group` | Sub2API 已打 key-group 补丁 | 返回 `schema_version=1` 的 `sub2api.key_group` 文档，含 `key` / `group` / `models` / `revision` |
| 回退 | `GET {baseUrl}/models` | 任意 OpenAI 兼容部署（Sub2API 已按 Key 分组白名单过滤） | **恰好 1 个**模型 id 时采纳；0 个或多个视为歧义 |

```text
                ┌──────────────────────────────────────────┐
                │  before_model_resolve / 后台刷新（每轮） │
                └───────────────────┬──────────────────────┘
                                    │
                    GET {baseUrl}/sub2api/key-group
                                    │
                 ┌──────────────────┴───────────────────┐
         2xx + schema_version=1                 404 / 405 / 501
         object=sub2api.key_group                        │
                 │                                        ▼
                 │                        GET {baseUrl}/models
                 │                                        │
                 │                     ┌──────────────────┴─────────────────┐
                 │              恰好 1 个 id                     0 个               ≥2 个
                 │                     │                        │                │
                 ▼                     ▼                        ▼                ▼
        按分组 platform 选传输   按上次已知 platform      保留上一次        权威歧义：
        （见 §4）+ 原 baseUrl    （缺省 openai-completions） 有效模型        不写配置、
                                  + 原 baseUrl              + 诊断         不覆盖每轮
```

要点：回退端点**只认单模型**。多个 id 说明该 Key 的白名单并不指向唯一上游，属于歧义，不得随机挑选。

歧义不是故障，而是「该 Key 不再是单模型 Key」的**权威结论**：此时插件不写配置、也不覆盖每轮模型选择，`openclaw.json` 里的 `primary` 继续生效，避免把机器人钉在一个上游已经 502/404 的目标上。

## 3. 解析规则（首选端点）

| 规则 | 说明 |
| --- | --- |
| 分组必须 active | `group.status === "active"`，否则整次发现失败（保留旧值） |
| 身份一致 | `key.group_id === group.id`，不一致即拒绝 |
| 单一真实模型 | `models` 长度必须为 1 |
| 别名归一 | 多个别名指向**同一上游** `upstream_model` 时归一为一个模型；别名不得引入第二个上游 |
| 别名可请求 | 模型 `id` 必须在自身 `aliases` 内 |
| 保留首选别名 | `preferredAlias` 命中时，保留该可请求别名作为最终 `id`（仅换名，不换目标） |

## 4. 传输选择

| 分组 `platform` | OpenClaw `api` | `baseUrl` 处理 |
| --- | --- | --- |
| `openai` / `deepseek` | `openai-completions` | 原样使用 |
| `anthropic` | `anthropic-messages` | 原样使用 |
| `gemini` | `google-generative-ai` | 去掉结尾 `/v1` |
| `/models` 回退 | `openai-completions` | 一律原 `baseUrl` |

其他平台无对应传输，视为发现失败（保留旧值）。

## 5. 写回范围

写入使用**原子替换**（临时文件 + `rename`，权限 `0600`，目录 `0700`），并在变更前保留备份。

| 配置位置 | 内容 |
| --- | --- |
| `models.providers.<p>.models` | 归一后的单个模型条目（含 `api`、`baseUrl`） |
| `models.providers.<p>.baseUrl` / `.api` | 与上表传输选择一致 |
| `agents.defaults.models` | `sub2api/<id>` 条目，清理旧的 `sub2api/*` 具体条目（保留 `sub2api/*` 通配） |
| `agents.defaults.model.primary` | `sub2api/<id>` |
| `agents.defaults.model.fallbacks` | 置空（分组只对应一个真实模型） |
| `agents/<id>/agent/models.json`（旧版每 agent 目录） | 同步写入同一模型目录 |
| `~/.openclaw/state/sub2api-auto.json` | 见 §7 |

## 6. 失败语义

| 情形 | 行为 |
| --- | --- |
| 接口错误 / HTTP 非 2xx | 不清空配置、不随机切换，保留最后有效模型 |
| 断网 / 超时（默认 4000ms） | 同上 |
| key-group 返回 5xx | 同上；**不**回退到 `/models`，避免把上游故障误判成「端点不存在」 |
| `/models` 返回 0 个模型（空组） | 同上（`empty-model-list`，保留旧值，不算歧义） |
| 空组（0 模型） | 同上 |
| 多模型（≥2） | **权威歧义**：不写配置、不覆盖每轮选择，仅保留缓存与本地诊断；配置中的 `primary` 保持权威 |

> 歧义期间状态文件的 `checked_at` **不会**刷新（因为没有提交任何快照），这是预期行为；
> 判断"插件还活着"请看网关日志里的 `non-unique-model` 行（每小时一条）。
| 分组非 active / 身份不一致 / 平台不支持 | 同上 |
| 诊断输出 | 仅写**本地网关诊断**（含错误码、时间、是否保留旧值），不含 URL、响应体、Key |

**绝不**通过 QQ 发送本功能的系统消息或诊断；普通模型回复与必要的用户可见错误不受影响。

## 7. 运维状态与并发

状态文件：`~/.openclaw/state/sub2api-auto.json`

| 字段 | 含义 |
| --- | --- |
| `key_id` | Key 在 Sub2API 侧的 id（非 Key 原文） |
| `fingerprint` | Key 的 SHA-256 指纹，用于识别 Key/地址身份 |
| `base_url` | 绑定该状态的连接地址 |
| `group` / `model` | 归一后的分组与模型（含 `upstream_model`、`aliases`） |
| `revision` | 服务端分组/模型修订号 |
| `generation` | 单调递增，用于**拒绝过期的后台响应**（旧响应不得覆盖新分组结果） |
| `checked_at` | 最近一次成功发现时间（排错关键字段） |

并发控制：

- 跨进程锁：`sub2api-auto.json.lock`（内核 `flock`，进程崩溃自动释放，不留假锁）。
- 序号文件：`sub2api-auto.json.sequence.json`，提供单调 `generation`。
- 事务日志：`sub2api-auto.json.transaction.json`，崩溃后按 `state_before/config_before` 恢复；若检测到用户手工改动则放弃自动恢复并报冲突。

## 8. 如何确认生效（只读）

以下命令只输出状态码或非敏感元数据，**不回显 Key**。先按需导出变量（不要把值贴进终端历史共享区）：

```bash
# 变量来自 profile 的 .env；此处不打印其值
BASE="$(grep -m1 '^SUB2API_API_URL=' /root/.openclaw/.env | cut -d= -f2-)"
KEY="$(grep -m1 '^SUB2API_API_KEY=' /root/.openclaw/.env | cut -d= -f2-)"
```

1) key-group 端点是否可用（只看状态码：期望 200；404 表示未打补丁 → 会走回退）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" "$BASE/sub2api/key-group"
```

2) 端点返回的分组元数据（非敏感字段；期望 `schema_version=1`、`status=active`、`group_id==group.id`、模型数=1）：

```bash
curl -s -H "Authorization: Bearer $KEY" "$BASE/sub2api/key-group" \
  | jq -r '[.schema_version, .group.platform, .group.status, (.key.group_id==.group.id), (.models|length)] | @tsv'
```

3) 回退端点的模型数量（期望 `1`；0 或 ≥2 即歧义）：

```bash
curl -s -H "Authorization: Bearer $KEY" "$BASE/models" | jq -r '[.data[].id] | length'
```

4) 状态文件是否新鲜、是否含 Key 原文（期望 `has_key=false`，`checked_at` 为近 1 分钟内）：

```bash
jq -r '[.checked_at, .generation, .revision, has("key")] | @tsv' /root/.openclaw/state/sub2api-auto.json
stat -c '%a %n' /root/.openclaw/state/sub2api-auto.json    # 期望 600
```

5) 配置是否已指向当前分组模型（`primary` 应与状态文件 `model.id` 一致）：

```bash
jq -r '.agents.defaults.model.primary' /root/.openclaw/openclaw.json
```

## 9. 验收与排错表

| 症状 | 优先检查 | 判定 |
| --- | --- | --- |
| QQ 回复 `model_not_found` | 配置 `primary` 与状态文件 `model.id` 是否一致；`/sub2api/key-group` 是否 404 | 404 → 走 `/models` 回退；不一致 → 检查写回是否被用户编辑打断 |
| 日志 `non-unique-model` 持续出现 | `/v1/models` 返回的 id 数量 | ≥2 → 该 Key 未绑定单模型分组（权威歧义，插件不覆盖）；需要自动同步时请把 Key 归入单模型分组 |
| 日志 `ingress_reject_reason=model_not_allowed` | 状态文件 `checked_at` 是否陈旧；`/v1/models` 是否只返回 1 个 id | 陈旧 → 后台刷新失败（查网络/锁）；模型数≠1 → 分组白名单非唯一 |
| 换了分组但机器人未切换 | `group.id` 是否变化、`revision` 是否递增、`generation` 是否推进 | `revision` 未变 → 服务端未刷新；`generation` 未推进 → 后台未运行 |
| 频繁告警 | 诊断错误码（`http-*` / `discovery-unavailable` / `non-unique-model`） | 按错误码定位，不看响应体（可能含敏感信息） |
| 状态被旧值覆盖 | 是否多进程共用同一 profile；`.lock` / `.sequence.json` 是否可写 | 锁目录不可写 → 检查属主与权限（目录 `0700`） |

常见错误码：`invalid-schema`、`missing-group`、`inactive-group`、`inconsistent-group`、`non-unique-model`、`invalid-model`、`unsupported-platform`、`http-<code>`、`discovery-unavailable`、`superseded-identity`、`coordination-*`、`persistence-*`。

## 10. 边界与约束

- 不改共享生产组、不改生产 Key 归属；仅使用机器人自己的 Key 做只读发现。
- 只覆盖 `sub2api` provider 的受管实例；显式选择其他 provider 或关闭 `sub2api-auto` 插件时，其会话模型不被覆盖。
- 已部署的 QQ thinking 提示过滤继续生效；本功能不发送任何 QQ 系统消息。
- 152（Node 26 / 新版 OpenClaw）与 153（Node 22 / OpenClaw 2026.6.11）行为一致；旧版无 `hooks.allowConversationAccess` 字段时仅省略该字段。
