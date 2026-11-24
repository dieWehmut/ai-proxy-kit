# OpenClaw 运行配置的可审查同步

生产 `openclaw.json` 同时包含普通设置和运行凭据。直接复制到仓库会把 QQ
`accessToken`、网关 `auth.token` 或模型 API Key 暴露给所有仓库读者。
`scripts/export-openclaw-profile.py` 生成的是**结构化脱敏 profile**，用于审查、
比对和灾难恢复，不是可直接启动的配置。

## 导出

在目标 LXC 内复制运行配置后，只在本机执行导出：

```bash
install -m 600 /root/.openclaw/openclaw.json /tmp/openclaw-152.json
python3 scripts/export-openclaw-profile.py \
  /tmp/openclaw-152.json \
  deploy/openclaw-152/openclaw.sanitized.json
rm -f /tmp/openclaw-152.json
```

脚本会：

- 深度复制配置，不修改源文件；
- 将所有字符串型 `token`、`accessToken`、`apiKey`、`password`、`secret`、
  `cookie`、`authorization` 值替换为 `<redacted>`；
- 保留 `{"source": "env", "id": "SUB2API_API_KEY"}` 这类环境变量引用，因为
  它不是凭据本身；
- 不再保留 `wizard.lastRunAt` 这类每次运行都会变化的噪声；
- 扫描输出中的 JWT、`sk-*` 和 32 位以上十六进制随机串，发现残留即失败。

## 不应提交

以下内容永远不要放进公开或共享仓库：

- 完整的 `~/.openclaw/.env`；
- `credentials/`、`identity/`、`devices/` 和会话正文；
- QQ 网关 access token、OpenClaw gateway token；
- Sub2API、Antigravity、AI Studio 的 Key 或密码；
- 浏览器 Cookie、Playwright auth 文件、设备私钥。

脱敏 profile 中的 `<redacted>` 必须由目标机器的 `.env` 和本地凭据目录重新提供。
部署脚本只能读取本机权限为 `600` 的私有文件，不能把凭据写进命令行或日志。

## 审阅

```bash
python3 -m unittest tests.test_openclaw_profile -v
python3 scripts/export-openclaw-profile.py --help
git diff --check
rg -n 'sk-|BEGIN (RSA|OPENSSH|PRIVATE)|"token"\s*:\s*"[^<]' deploy/
```

如果需要在两个实例之间比较，只比较 profile：

```bash
diff -u deploy/openclaw-152/openclaw.sanitized.json \
        deploy/openclaw-153/openclaw.sanitized.json || true
```

差异应能逐项解释，例如 Node/OpenClaw 版本、QQ 会话策略或实例专属模型。
