"use strict";

const path = require("node:path");
const { installNovnc } = require("./novnc.cjs");

const novnc = installNovnc({
  sourceDir: path.join(__dirname, "vendor/novnc"),
  distDir: path.join(process.cwd(), "ui/dist"),
});

console.info("[AIProxyKit] noVNC self-host assets ready: " + JSON.stringify(novnc));
