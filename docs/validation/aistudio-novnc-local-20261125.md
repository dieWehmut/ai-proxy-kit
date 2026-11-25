# AI Studio noVNC 本地化验证（2026-11-25）

> **历史：Build 通道已停用。** 本记录只说明已移除的 noVNC 自托管实现。当前服务为
> Compose 服务 `aistudio`（AIStudio2API Playground，容器端口 2048），不再提供 noVNC。
> 当前迁移证据见
> [aistudio-playground-migration-20261125.md](aistudio-playground-migration-20261125.md)。

## 部署

```
docker compose --env-file runtime/stack.env --profile aistudio -f compose.yml -f compose.aistudio-novnc.yml up -d aistudio
```

执行后容器 `ai-proxy-kit-aistudio-1` 被 Recreate，并在 12 秒内进入 healthy 状态。

## 容器日志

容器日志出现：

```
[AIProxyKit] noVNC self-host assets ready: {"targetDir":"/app/ui/dist/vendor/novnc","rewrittenBundles":1,"alreadyPatchedBundles":0,"replacements":1}
```

## 容器内 grep

在容器内对 `/app/ui/dist` 检索：

- 提到 `esm.sh/@novnc` 的仅剩 `/app/ui/dist/vendor/novnc/PROVENANCE.md`（文档）。
- `assets/*.js` 中已无 `esm.sh`。
- `/vendor/novnc/core/rfb.js` 引用计数为 1。

## 结论

7860 前端不再依赖 esm.sh，noVNC 由本地 vendor 提供。

测试结果：`python3 -m unittest discover` 74 项通过，`node --test tests/test_aistudio_novnc.cjs` 10 项通过。

本记录只包含本次实测事实，不包含 key、邮箱或 cookie。
