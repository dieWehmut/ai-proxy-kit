"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  NOVNC_CDN_URL,
  NOVNC_PUBLIC_URL,
  installNovnc,
} = require("../services/aistudio-hydrogen/novnc.cjs");

const projectRoot = path.resolve(__dirname, "..");
const vendorDir = path.join(projectRoot, "services/aistudio-hydrogen/vendor/novnc");

function makeDistFixture() {
  const distDir = fs.mkdtempSync(path.join(os.tmpdir(), "aistudio-novnc-"));
  fs.mkdirSync(path.join(distDir, "assets"), { recursive: true });
  return distDir;
}

test("vendored noVNC 1.4.0 contains the native ESM entrypoint and pako dependency", () => {
  const core = fs.readFileSync(path.join(vendorDir, "core/rfb.js"), "utf8");
  const inflator = fs.readFileSync(path.join(vendorDir, "core/inflator.js"), "utf8");

  assert.match(core, /export default class RFB extends EventTargetMixin/);
  assert.match(inflator, /from "\.\.\/vendor\/pako\/lib\/zlib\/inflate\.js"/);
  assert.ok(fs.existsSync(path.join(vendorDir, "lib/rfb.js")), "package-parity lib tree is missing");
  assert.match(fs.readFileSync(path.join(vendorDir, "LICENSE.txt"), "utf8"), /MPL 2\.0/);
  assert.ok(fs.existsSync(path.join(vendorDir, "docs/LICENSE.MPL-2.0")));
});

test("startup installer copies noVNC and rewrites the exact CDN import idempotently", () => {
  const distDir = makeDistFixture();
  const bundlePath = path.join(distDir, "assets/index-test.js");
  const browserBundle = `const load=()=>import("${NOVNC_CDN_URL}");export default load;`;
  fs.writeFileSync(bundlePath, browserBundle);

  try {
    const first = installNovnc({ sourceDir: vendorDir, distDir });
    assert.equal(first.rewrittenBundles, 1);
    assert.equal(first.replacements, 1);
    assert.ok(fs.existsSync(path.join(distDir, "vendor/novnc/core/rfb.js")));
    assert.ok(fs.existsSync(path.join(distDir, "vendor/novnc/vendor/pako/lib/zlib/inflate.js")));
    assert.ok(fs.existsSync(path.join(distDir, "vendor/novnc/LICENSE.txt")));

    const patched = fs.readFileSync(bundlePath, "utf8");
    assert.ok(patched.includes(NOVNC_PUBLIC_URL));
    assert.ok(!patched.includes(NOVNC_CDN_URL));

    const second = installNovnc({ sourceDir: vendorDir, distDir });
    assert.equal(second.rewrittenBundles, 0);
    assert.equal(second.alreadyPatchedBundles, 1);
    assert.equal(second.replacements, 0);
  } finally {
    fs.rmSync(distDir, { recursive: true, force: true });
  }
});

test("startup installer refuses a UI bundle without the expected noVNC import", () => {
  const distDir = makeDistFixture();
  fs.writeFileSync(path.join(distDir, "assets/index-test.js"), "console.log('unrelated');");

  try {
    assert.throws(
      () => installNovnc({ sourceDir: vendorDir, distDir }),
      /refusing to serve a CDN-dependent UI/,
    );
  } finally {
    fs.rmSync(distDir, { recursive: true, force: true });
  }
});
