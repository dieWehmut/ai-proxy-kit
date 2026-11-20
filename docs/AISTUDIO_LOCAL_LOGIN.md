# 复用本机 Google 登录

AIStudioToAPI 可以导入 Playwright 会话文件。本机 Chrome/Edge 已登录的 Google
账号可通过下面的小扩展导出，无需在 Docker/VNC 内重新输入密码。
是否能继续使用该会话，仍以导入后的实际 Google 页面和生成验证为准。

1. 在已经登录 Google 的 Chrome/Edge 资料中，打开 `chrome://extensions` / `edge://extensions`。
2. 打开“开发者模式”，点击“加载已解压的扩展程序”，选择本仓库的
   `tools/google-session-extension` 目录。
3. 扩展会打开“复用本机 Google 登录”页面，点击“导出当前 Google 会话”，得到
   `ai-proxy-kit-google-session.json`。
4. 在仓库所在 Linux/WSL 运行：

   ```bash
   python3 scripts/import-aistudio-session.py /path/to/ai-proxy-kit-google-session.json
   bash check.sh aistudio
   # 浏览器连接成功后，验证通过 sub2api 的真实生成。
   bash connect.sh --provider aistudio --test
   ```

导入脚本写入权限为 `600` 的 `runtime/aistudio/auth/auth-N.json`，并使用本机控制台
密码通知服务重载。重复导入同一份文件会复用现有记录，已有账号不会被覆盖。
可使用 `--wait 600` 在终端等待浏览器生成导出文件。

扩展只有 Google 域名的 Cookie 权限，不发送网络请求。导出文件含登录会话，
无需发到聊天或上传到 GitHub。完成导入后可删除下载副本、移除扩展。
仓库已忽略该导出文件名及整个 `runtime/` 目录。

只有本机浏览器资料已登录，但对应 Google 网页会话失效时，浏览器导出可能仍不可用；
导入成功也不代替 `browserConnected` 和实际生成验证。

浏览器接口依据：[Chrome Cookie API](https://developer.chrome.com/docs/extensions/reference/api/cookies)、
[加载本地扩展](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked)。
