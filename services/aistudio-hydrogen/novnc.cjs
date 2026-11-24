"use strict";

const fs = require("node:fs");
const path = require("node:path");

// The AI Studio v1.4.1 SPA imports this exact URL at runtime. The npm package
// entrypoint is CommonJS, so the browser-facing replacement is noVNC's native
// ES module entrypoint under core/.
const NOVNC_CDN_URL = "https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js";
const NOVNC_PUBLIC_URL = "/vendor/novnc/core/rfb.js";

const REQUIRED_VENDOR_FILES = [
  "LICENSE.txt",
  "docs/LICENSE.MPL-2.0",
  "lib/rfb.js",
  "core/rfb.js",
  "vendor/pako/lib/zlib/inflate.js",
];

function requireDirectory(directory, label) {
  let stat;
  try {
    stat = fs.statSync(directory);
  } catch {
    throw new Error(`${label} does not exist: ${directory}`);
  }
  if (!stat.isDirectory()) throw new Error(`${label} is not a directory: ${directory}`);
}

function requireVendorTree(sourceDir) {
  for (const relativePath of REQUIRED_VENDOR_FILES) {
    if (!fs.existsSync(path.join(sourceDir, relativePath))) {
      throw new Error(`The noVNC vendor tree is incomplete: missing ${relativePath}`);
    }
  }
}

function rewriteBundle(bundlePath) {
  const source = fs.readFileSync(bundlePath, "utf8");
  const replacements = source.split(NOVNC_CDN_URL).length - 1;

  if (replacements === 0) {
    return {
      bundlePath,
      status: source.includes(NOVNC_PUBLIC_URL) ? "already" : "unrelated",
      replacements: 0,
    };
  }

  // Rename a sibling temporary file so the static server never observes a
  // partially rewritten JavaScript bundle during startup.
  const temporaryPath = `${bundlePath}.novnc-${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, source.split(NOVNC_CDN_URL).join(NOVNC_PUBLIC_URL), "utf8");
  fs.renameSync(temporaryPath, bundlePath);
  return { bundlePath, status: "rewritten", replacements };
}

function installNovnc({ sourceDir, distDir }) {
  requireDirectory(sourceDir, "The noVNC vendor directory");
  requireDirectory(distDir, "The AI Studio static directory");
  requireVendorTree(sourceDir);

  const targetDir = path.join(distDir, "vendor", "novnc");
  fs.mkdirSync(targetDir, { recursive: true });
  fs.cpSync(sourceDir, targetDir, { recursive: true, force: true });

  const assetsDir = path.join(distDir, "assets");
  requireDirectory(assetsDir, "The AI Studio asset directory");
  const bundles = fs.readdirSync(assetsDir, { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.endsWith(".js"))
    .map(entry => path.join(assetsDir, entry.name));

  const results = bundles.map(rewriteBundle);
  const rewritten = results.filter(result => result.status === "rewritten");
  const already = results.filter(result => result.status === "already");

  if (rewritten.length === 0 && already.length === 0) {
    throw new Error(`Cannot find the noVNC import in ${assetsDir}; refusing to serve a CDN-dependent UI.`);
  }

  return {
    targetDir,
    rewrittenBundles: rewritten.length,
    alreadyPatchedBundles: already.length,
    replacements: rewritten.reduce((total, result) => total + result.replacements, 0),
  };
}

module.exports = {
  NOVNC_CDN_URL,
  NOVNC_PUBLIC_URL,
  installNovnc,
  rewriteBundle,
};
