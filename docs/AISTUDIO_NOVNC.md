# AI Studio 自托管 noVNC

AI StudioToAPI `v1.4.1` 的网页控制台在构建产物中硬编码了：

```text
https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js
```

这会让 VNC 登录页在运行时依赖第三方 CDN。`services/aistudio-hydrogen`
现在把 noVNC 固定版本复制到镜像的静态目录，并在 Express 启动前改写该 URL。

## 实现

- 依赖来源：npm `@novnc/novnc@1.4.0`，tarball SHA-256
  `005d4676126a252fcdf6e4851433dc7913947542b7f8f8b941af594c9dcd94cc`。
- 内置位置：`services/aistudio-hydrogen/vendor/novnc`，包含 `lib/`、`core/`、
  `vendor/pako/`、`LICENSE.txt`、上游 `AUTHORS` 和许可证文本。
- 启动时：`preload.cjs` 在加载上游 Express 应用前调用 `installNovnc()`。
- 复制目标：`/app/ui/dist/vendor/novnc`。
- URL 改写：CDN 地址替换为 `/vendor/novnc/core/rfb.js`。
- 重写是幂等的；容器重启后若已改写，会记录 `alreadyPatchedBundles=1`。
- 如果固定版本中既找不到 CDN URL、也没有本地 URL，启动会失败，避免静默回退到 CDN。

这里使用 `core/rfb.js` 而不是 npm 包中的 `lib/rfb.js`：`lib/` 是
CommonJS 转译产物，浏览器原生 `import()` 无法直接执行；`core/` 是带
`export default RFB` 的 ES module。`lib/` 仍完整保留以便与 npm 包核对。

## 本地验证

```bash
node --test tests/test_aistudio_novnc.cjs
node tests/test_aistudio_hydrogen.cjs
node --check services/aistudio-hydrogen/novnc.cjs
node --check services/aistudio-hydrogen/preload.cjs
```

在本地 AI Studio 容器内可检查启动日志和静态文件：

```bash
docker compose --profile aistudio up -d aistudio
docker compose logs aistudio | grep 'noVNC self-host'
docker compose exec aistudio sh -c \
  '! grep -R "esm.sh/@novnc" /app/ui/dist/assets && test -s /app/ui/dist/vendor/novnc/core/rfb.js'
curl -fsSI http://127.0.0.1:7860/vendor/novnc/core/rfb.js
```

启动日志应包含类似：

```text
[AIProxyKit] noVNC self-host assets ready: {"targetDir":"/app/ui/dist/vendor/novnc","rewrittenBundles":1,"alreadyPatchedBundles":0,"replacements":1}
```

该改写发生在容器启动时，因此不会修改本地镜像；每次新容器启动都会从镜像中的
原始 SPA 重新复制并改写。升级 AI Studio 时必须重新核对硬编码 URL 和版本检查。

## 实机验证

- [LXC 158 旧 7860 栈的 noVNC 自托管验证](validation/aistudio-lxc158-novnc-20261124.md)
