"use strict";

const fs = require("node:fs");
const path = require("node:path");

const NOVNC_CDN_URL = "https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js";
const NOVNC_PUBLIC_URL = "/vendor/novnc/core/rfb.js";
const NOVNC_CDN_MARKERS = ["esm.sh/@novnc", "@novnc/novnc", NOVNC_CDN_URL];

const REQUIRED_VENDOR_FILES = [
  "LICENSE.txt",
  "AUTHORS",
  "docs/LICENSE.MPL-2.0",
  "lib/rfb.js",
  "core/rfb.js",
  "vendor/pako/LICENSE",
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

function makeTreeWritable(root) {
  let stat;
  try {
    stat = fs.lstatSync(root);
  } catch {
    return;
  }

  if (stat.isDirectory()) {
    fs.chmodSync(root, stat.mode | 0o700);
    for (const entry of fs.readdirSync(root)) {
      makeTreeWritable(path.join(root, entry));
    }
  } else {
    fs.chmodSync(root, stat.mode | 0o600);
  }
}

function findCdnReferences(source) {
  return NOVNC_CDN_MARKERS.filter(marker => source.includes(marker));
}

function listJavaScriptFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...listJavaScriptFiles(entryPath));
    } else if (entry.isFile() && entry.name.endsWith(".js")) {
      files.push(entryPath);
    }
  }
  return files.sort();
}

function rewriteBundle(bundlePath) {
  const source = fs.readFileSync(bundlePath, "utf8");
  const replacements = source.split(NOVNC_CDN_URL).length - 1;

  if (replacements === 0) {
    if (findCdnReferences(source).length > 0) {
      return { bundlePath, status: "cdn-reference", replacements: 0 };
    }
    const status = source.includes(NOVNC_PUBLIC_URL) ? "already" : "unrelated";
    return { bundlePath, status, replacements: 0 };
  }

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
  if (fs.existsSync(targetDir)) {
    makeTreeWritable(targetDir);
    fs.rmSync(targetDir, { recursive: true, force: true });
  }
  fs.mkdirSync(targetDir, { recursive: true });
  fs.cpSync(sourceDir, targetDir, { recursive: true, force: true });

  const assetsDir = path.join(distDir, "assets");
  requireDirectory(assetsDir, "The AI Studio asset directory");
  const bundles = listJavaScriptFiles(assetsDir);

  const results = bundles.map(rewriteBundle);

  const residualReferences = bundles
    .map(bundlePath => ({ bundlePath, references: findCdnReferences(fs.readFileSync(bundlePath, "utf8")) }))
    .filter(result => result.references.length > 0);
  if (residualReferences.length > 0) {
    const detail = residualReferences
      .map(({ bundlePath, references }) => {
        const relativePath = path.relative(distDir, bundlePath).split(path.sep).join("/");
        return `${relativePath} (${references.join(", ")})`;
      })
      .join("; ");
    throw new Error(
      `noVNC CDN references remain in AI Studio assets after installation: ${detail}. ` +
      "Refusing to serve a CDN-dependent UI.",
    );
  }

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
  NOVNC_CDN_MARKERS,
  NOVNC_PUBLIC_URL,
  installNovnc,
  rewriteBundle,
};
