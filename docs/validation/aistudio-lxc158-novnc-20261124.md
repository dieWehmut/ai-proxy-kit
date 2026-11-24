# LXC 158 AI Studio noVNC 自托管验证

日期：2026-11-24（UTC）  
目标：将 `fix/aistudio-novnc-selfhost` 部署到 LXC 158 的旧 AI Studio 7860 栈，
消除浏览器运行时对 `https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js` 的依赖。

## 结论

部署成功。旧 7860 容器已重启并恢复 healthy，启动时加载本地 noVNC 资产：

```text
[AIProxyKit] noVNC self-host assets ready: {"targetDir":"/app/ui/dist/vendor/novnc","rewrittenBundles":1,"alreadyPatchedBundles":0,"replacements":1}
```

- 服务 URL：`http://192.168.50.10:7860/`
- 本地模块 URL：`http://192.168.50.10:7860/vendor/novnc/core/rfb.js`
- 页面入口：`/login`（未进行控制台或 Google 登录）

## 变更边界

只操作 LXC 158 的旧 AI Studio 容器：

- LXC 158：唯一部署目标。
- LXC 152 / 153：未进入、未修改；只读 `pct list` 显示仍为 running。
- 7870 Playground：未重启、未改端口、未改配置。部署前后容器 ID 和启动时间一致：
  - ID：`2a0928ff7a32cdc00dbfb9b2cec368e8b0a336c71e4daf474dce597cb491be7f`
  - StartedAt：`2026-11-24T14:25:05.692592879Z`
- 未修改 Compose 文件；现有 `compose.aistudio-hydrogen.yml` 已提供正确的
  `/app/proxy-kit` 挂载和 `preload.cjs` 启动命令。
- 未输出任何 cookie、token、API Key 或控制台密码。

## 部署方式

- 部署源码：分支 `fix/aistudio-novnc-selfhost`，提交 `114bbef`。
- 部署目录：`/opt/ai-proxy-kit-aistudio/services/aistudio-hydrogen`。
- 备份目录：
  `/opt/ai-proxy-kit-aistudio/runtime/backups/aistudio-hydrogen-pre-novnc-20261124T153109Z`
- 注入文件：
  - `preload.cjs`
  - `novnc.cjs`
  - `vendor/novnc/`（103 个文件，包含 `lib/`、`core/`、`vendor/pako/`、许可证）
- 仅重启目标容器：`docker restart ai-proxy-kit-aistudio-aistudio-1`。

容器保持同一 ID，镜像、端口和重启策略未变：

| 项目 | 部署前 | 部署后 |
| --- | --- | --- |
| Container ID | `ad580446f35ccc6cac5dd36dbdd03140fb00417fe1483979c46f6f8d7d133777` | 同左（restart 保留容器） |
| StartedAt | `2026-11-24T05:50:24.071127902Z` | `2026-11-24T15:32:18.350039677Z` |
| 镜像 ID | `sha256:8608b7f50f2cd7863d2b61c6ba3f7869fca689ba3ef5dd90a8507207c17b67a3` | 同左 |
| 端口 | `0.0.0.0:7860->7860/tcp` | 同左 |
| Health | `healthy` | `healthy` |
| `/app/ui/dist/vendor` | 不存在 | 存在 |
| SPA esm.sh 引用数 | 1 | 0 |
| SPA 本地 noVNC 引用数 | 0 | 1 |
| 启动日志 | 无 noVNC self-host 行 | `rewrittenBundles:1, replacements:1` |

部署后的关键文件哈希：

```text
preload.cjs  08664dd5506cd3756fe4d328bbf44bd518f99a45bd72701299d2ab97341d6827
novnc.cjs    6eaae1b06a896e93b1b9163908f4580c7c14f41ed93772affded25535ac97cd1
core/rfb.js  f497c8b080f76aee1667a3c41db61be178f93e9ce4c39d150b8bcdbefdc2b0ca
LICENSE.txt  e522f81d37206b944461b6b50bc79bd61ccc838b442ba51871f9566de531f5c4
```

## HTTP 与静态资产证据

从 LXC 158 本机请求：

```text
/health                           status=200 type=application/json; charset=utf-8
/                                 status=200 type=text/html; charset=UTF-8
/assets/index-DbG4zzH3.js         status=200 type=application/javascript; charset=UTF-8 bytes=1288279
/vendor/novnc/core/rfb.js         status=200 type=application/javascript; charset=UTF-8 bytes=119862
```

从 Windows 主机请求同一 7860 地址：

```text
health=200
vendor=200 type=application/javascript; charset=UTF-8 bytes=119862
```

容器内资产检查：

```text
esm_refs=0
local_refs=1
vendor_rfb_bytes=119862
vendor_pako_bytes=47281
package_version=1.4.1
```

## 真实浏览器 / Playwright 证据

使用 Playwright Chromium 打开 `http://192.168.50.10:7860/`，页面跳转到
`http://192.168.50.10:7860/login`，标题为 `Google AI Studio Proxy - Login`。
在真实页面上下文中执行：

```javascript
const module = await import('/vendor/novnc/core/rfb.js');
```

结果：

```text
title=NOVNC_IMPORT_OK
defaultType=function
className=RFB
bundleStatus=200
bundleTextLength=1287190
esmReferencesInServedBundle=0
localReferencesInServedBundle=1
novncResourceRequests=45
esmResourceRequests=0
browserConsoleErrors=0
```

浏览器请求记录显示 45 个 `/vendor/novnc/` 模块请求全部为 HTTP 200，包括
`core/rfb.js`、`core/*` 和 `vendor/pako/*`；没有 `esm.sh` 请求。

本次未输入控制台密码，也未发起 Google 登录，因此没有建立真实 VNC WebSocket
会话。验证边界是登录页 UI、实际 SPA 静态资源和 noVNC ES module 的动态加载；
这与 handoff 允许的“至少验证静态模块和 UI 网络请求”一致。

## 回滚

```bash
backup=/opt/ai-proxy-kit-aistudio/runtime/backups/aistudio-hydrogen-pre-novnc-20261124T153109Z
cp -a "$backup/." /opt/ai-proxy-kit-aistudio/services/aistudio-hydrogen/
docker restart ai-proxy-kit-aistudio-aistudio-1
```

回滚会恢复旧 `preload.cjs`，因此新增的 `novnc.cjs` 和 `vendor/novnc/` 即使仍留在
目录中也不会被加载；容器 ID 和 7860 端口保持不变。如需完全恢复原目录，可在恢复
`preload.cjs` 后手动移除这两个新增路径。
