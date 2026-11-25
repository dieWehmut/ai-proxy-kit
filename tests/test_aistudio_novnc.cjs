"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");
const {
  NOVNC_CDN_URL,
  NOVNC_PUBLIC_URL,
  installNovnc,
  rewriteBundle,
} = require("../services/aistudio-novnc/novnc.cjs");

const projectRoot = path.resolve(__dirname, "..");
const serviceDir = path.join(projectRoot, "services/aistudio-novnc");
const vendorDir = path.join(serviceDir, "vendor/novnc");
const preloadPath = path.join(serviceDir, "preload.cjs");

function makeFixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aistudio-novnc-test-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const distDir = path.join(root, "ui/dist");
  const assetsDir = path.join(distDir, "assets");
  fs.mkdirSync(assetsDir, { recursive: true });
  return { root, distDir, assetsDir };
}

test("vendored noVNC 1.4.0 includes its native ESM dependency tree and licenses", () => {
  const pending = [path.join(vendorDir, "core/rfb.js")];
  const visited = new Set();
  const importPattern = /(?:^|\n)[ \t]*(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+)["']([^"']+)["']/g;
  while (pending.length > 0) {
    const modulePath = pending.pop();
    if (visited.has(modulePath)) continue;
    visited.add(modulePath);
    const source = fs.readFileSync(modulePath, "utf8");
    for (const match of source.matchAll(importPattern)) {
      assert.ok(match[1].startsWith("."), `Non-local import in ${modulePath}`);
      const dependency = path.resolve(path.dirname(modulePath), match[1]);
      assert.ok(dependency.startsWith(vendorDir + path.sep));
      assert.ok(fs.existsSync(dependency), `Missing dependency ${dependency}`);
      pending.push(dependency);
    }
  }

  assert.match(fs.readFileSync(path.join(vendorDir, "core/rfb.js"), "utf8"), /export default class RFB/);
  assert.ok(visited.has(path.join(vendorDir, "vendor/pako/lib/zlib/inflate.js")));
  for (const relativePath of [
    "lib/rfb.js",
    "AUTHORS",
    "LICENSE.txt",
    "docs/LICENSE.MPL-2.0",
    "docs/LICENSE.BSD-2-Clause",
    "docs/LICENSE.BSD-3-Clause",
    "docs/LICENSE.OFL-1.1",
    "vendor/pako/LICENSE",
    "PROVENANCE.md",
  ]) {
    assert.ok(fs.existsSync(path.join(vendorDir, relativePath)), `Missing ${relativePath}`);
  }
});

test("installer copies the vendor tree and rewrites all exact CDN imports idempotently", t => {
  const { distDir, assetsDir } = makeFixture(t);
  const bundlePath = path.join(assetsDir, "index-test.js");
  const browserBundle = `const model="gpt-6.2-sol";const load=()=>import("${NOVNC_CDN_URL}");const reload=()=>import('${NOVNC_CDN_URL}');`;
  fs.writeFileSync(bundlePath, browserBundle);
  fs.writeFileSync(path.join(assetsDir, "vnc-test.js"), `import("${NOVNC_CDN_URL}");`);
  fs.writeFileSync(path.join(assetsDir, "other.js"), "console.log('other');");
  fs.writeFileSync(path.join(assetsDir, "index.js.map"), NOVNC_CDN_URL);

  const first = installNovnc({ sourceDir: vendorDir, distDir });
  assert.equal(first.rewrittenBundles, 2);
  assert.equal(first.replacements, 3);
  assert.equal(first.alreadyPatchedBundles, 0);
  for (const relativePath of fs.readdirSync(vendorDir, { recursive: true })) {
    const sourcePath = path.join(vendorDir, relativePath);
    if (!fs.statSync(sourcePath).isFile()) continue;
    assert.deepEqual(fs.readFileSync(path.join(first.targetDir, relativePath)), fs.readFileSync(sourcePath));
  }
  const patched = browserBundle.split(NOVNC_CDN_URL).join(NOVNC_PUBLIC_URL);
  assert.equal(fs.readFileSync(bundlePath, "utf8"), patched);
  assert.equal(fs.readFileSync(path.join(assetsDir, "other.js"), "utf8"), "console.log('other');");
  assert.equal(fs.readFileSync(path.join(assetsDir, "index.js.map"), "utf8"), NOVNC_CDN_URL);

  const second = installNovnc({ sourceDir: vendorDir, distDir });
  assert.equal(second.rewrittenBundles, 0);
  assert.equal(second.alreadyPatchedBundles, 2);
  assert.equal(second.replacements, 0);
  assert.equal(fs.readFileSync(bundlePath, "utf8"), patched);
  assert.ok(fs.readdirSync(assetsDir).every(name => !name.endsWith(".tmp")));
});

test("bundle rewriter recognizes unrelated and already patched bundles", t => {
  const { assetsDir } = makeFixture(t);
  const bundlePath = path.join(assetsDir, "index-test.js");
  fs.writeFileSync(bundlePath, "console.log('other');");
  assert.deepEqual(rewriteBundle(bundlePath), { bundlePath, status: "unrelated", replacements: 0 });
  fs.writeFileSync(bundlePath, `import("${NOVNC_PUBLIC_URL}");`);
  assert.deepEqual(rewriteBundle(bundlePath), { bundlePath, status: "already", replacements: 0 });
  fs.writeFileSync(bundlePath, `import("${NOVNC_PUBLIC_URL}");import("https://esm.sh/@novnc/novnc@1.5.0/lib/rfb.js");`);
  assert.deepEqual(rewriteBundle(bundlePath), { bundlePath, status: "cdn-reference", replacements: 0 });
});

test("installer rejects a patched bundle when another bundle still references a noVNC CDN build", t => {
  const { distDir, assetsDir } = makeFixture(t);
  fs.writeFileSync(path.join(assetsDir, "index-patched.js"), `import("${NOVNC_PUBLIC_URL}");`);
  fs.writeFileSync(path.join(assetsDir, "vnc-new.js"), 'import("https://esm.sh/@novnc/novnc@1.5.0/lib/rfb.js");');

  assert.throws(
    () => installNovnc({ sourceDir: vendorDir, distDir }),
    /noVNC CDN references remain in AI Studio assets after installation: .*vnc-new\.js.*@novnc\/novnc/,
  );
  assert.ok(fs.existsSync(path.join(distDir, "vendor/novnc/core/rfb.js")));
});

test("installer succeeds when every bundle is free of noVNC CDN references", t => {
  const { distDir, assetsDir } = makeFixture(t);
  fs.writeFileSync(path.join(assetsDir, "index-patched.js"), `import("${NOVNC_PUBLIC_URL}");`);
  fs.writeFileSync(path.join(assetsDir, "other.js"), "console.log('other');");

  const result = installNovnc({ sourceDir: vendorDir, distDir });
  assert.equal(result.rewrittenBundles, 0);
  assert.equal(result.alreadyPatchedBundles, 1);
  assert.equal(result.replacements, 0);
});

test("installer accepts a read-only vendor source", t => {
  const { root, distDir, assetsDir } = makeFixture(t);
  const sourceDir = path.join(root, "source");
  fs.cpSync(vendorDir, sourceDir, { recursive: true });
  const entries = fs.readdirSync(sourceDir, { recursive: true }).map(entry => path.join(sourceDir, entry));
  const directories = [sourceDir, ...entries.filter(entry => fs.statSync(entry).isDirectory())];
  for (const entry of entries) {
    if (fs.statSync(entry).isFile()) fs.chmodSync(entry, 0o444);
  }
  for (const directory of directories) fs.chmodSync(directory, 0o555);
  fs.writeFileSync(path.join(assetsDir, "index-test.js"), `import("${NOVNC_CDN_URL}");`);

  try {
    assert.equal(installNovnc({ sourceDir, distDir }).rewrittenBundles, 1);
    assert.equal(installNovnc({ sourceDir, distDir }).alreadyPatchedBundles, 1);
  } finally {
    for (const directory of directories) fs.chmodSync(directory, 0o755);
    const targetDir = path.join(distDir, "vendor/novnc");
    for (const entry of fs.readdirSync(targetDir, { recursive: true })) {
      const targetPath = path.join(targetDir, entry);
      if (fs.statSync(targetPath).isDirectory()) fs.chmodSync(targetPath, 0o755);
    }
    fs.chmodSync(targetDir, 0o755);
  }
});

test("installer rejects an incomplete vendor tree", t => {
  const { root, distDir } = makeFixture(t);
  const sourceDir = path.join(root, "source");
  fs.cpSync(vendorDir, sourceDir, { recursive: true });
  fs.rmSync(path.join(sourceDir, "vendor/pako/LICENSE"));
  assert.throws(() => installNovnc({ sourceDir, distDir }), /missing vendor\/pako\/LICENSE/);
});

test("installer rejects a missing static asset directory", t => {
  const { distDir, assetsDir } = makeFixture(t);
  fs.rmSync(assetsDir, { recursive: true });
  assert.throws(() => installNovnc({ sourceDir: vendorDir, distDir }), /AI Studio asset directory does not exist/);
});

test("installer rejects a UI bundle without the expected noVNC import", t => {
  const { distDir, assetsDir } = makeFixture(t);
  fs.writeFileSync(path.join(assetsDir, "index-test.js"), "console.log('other');");
  assert.throws(() => installNovnc({ sourceDir: vendorDir, distDir }), /refusing to serve a CDN-dependent UI/);
});

test("preload installs noVNC before main.js runs", t => {
  const { root, assetsDir } = makeFixture(t);
  fs.writeFileSync(path.join(assetsDir, "index-test.js"), `import("${NOVNC_CDN_URL}");`);
  fs.writeFileSync(path.join(root, "main.js"), [
    'const assert = require("node:assert/strict");',
    'const fs = require("node:fs");',
    `assert.equal(fs.readFileSync("ui/dist/assets/index-test.js", "utf8"), 'import("${NOVNC_PUBLIC_URL}");');`,
    'assert.ok(fs.existsSync("ui/dist/vendor/novnc/core/rfb.js"));',
    'console.log("main-ready");',
  ].join("\n"));

  const result = spawnSync(process.execPath, ["--require", preloadPath, "main.js"], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /noVNC self-host assets ready/);
  assert.match(result.stdout, /main-ready/);
});

test("preload stops startup when the UI bundle is incompatible", t => {
  const { root, assetsDir } = makeFixture(t);
  fs.writeFileSync(path.join(assetsDir, "index-test.js"), "console.log('other');");
  fs.writeFileSync(path.join(root, "main.js"), 'console.log("main-ready");');
  const result = spawnSync(process.execPath, ["--require", preloadPath, "main.js"], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /refusing to serve a CDN-dependent UI/);
  assert.equal(result.stdout, "");
});

test("compose overlay contains the preload command and one read-only mount", () => {
  const overlay = fs.readFileSync(path.join(projectRoot, "compose.aistudio-novnc.yml"), "utf8");
  assert.equal(overlay, [
    "services:",
    "  aistudio:",
    "    command:",
    "      - node",
    "      - --require",
    "      - /app/proxy-kit/preload.cjs",
    "      - main.js",
    "    volumes:",
    "      - ./services/aistudio-novnc:/app/proxy-kit:ro",
    "",
  ].join("\n"));
});
