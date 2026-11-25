# AI Studio 登录诊断（2026-11-25）

## 结论

本次登录未成功，不能记录为生成验收通过。导入 Google cookie 后，容器只短暂显示
`browserConnected=true`，随后 `auth-0` 被判定为 expired，生成请求返回 HTTP 503。
`browserConnected=true` 不能作为登录成功的依据。

## 实测事实

1. 向容器导入 40 条 `google.com` cookie 后，容器短时间出现
   `browserConnected=true`。
2. 该状态没有保持，`auth-0` 随即被标记为 expired。
3. 使用该状态发起生成请求，返回 HTTP 503。
4. 失败页是 Google 的 **“Choose an account / Signed out”** 页面。
5. 本次失败不是 2FA 挑战，也不是 CAPTCHA 页面。

## 判读

- `browserConnected=true` 只表示浏览器连接曾建立，不能证明 Google 登录仍然有效。
- `auth-0` 被判 expired 后，当前账号不可用，生成返回 503 与此状态一致。
- 登录验收必须以 `/api/status` 为准：`currentAuthIndex` 必须指向有效账号，
  不能是 `-1`，且该账号不能处于 expired 或 invalid 状态。
- 仅看到 `/health` 或日志中的 `browserConnected=true` 时，不应继续把后续 503
  解释为已登录但生成偶发失败。

## 下一步

1. 打开 AI Studio 控制台的 `/login`，完成控制台认证后进入 VNC/浏览器画面。
2. 在 VNC 中手动完成 Google 登录，确认页面离开 “Choose an account / Signed out”
   状态。
3. 查询 `/api/status`，确认 `browserConnected=true`、`currentAuthIndex` 为有效账号，
   且当前账号未 expired、未 invalid。
4. 状态满足后，再发起一次最小生成请求做验收；只有生成返回成功，才能记录为登录和
   生成链路通过。

本记录只包含本次诊断事实，不包含邮箱、API key、cookie 或其他凭据，也不代表登录或
生成已经成功。
