"use strict";

// Small, version-checked adapter for the pinned upstream. It changes no browser
// credentials and records only model/path/status evidence, never headers or bodies.
const fs = require("node:fs");
const path = require("node:path");
const { MODEL, requireModel, generationModel, requireObservedModel } = require("./contract.cjs");
const root = process.cwd();
const version = require(path.join(root, "package.json")).version;
if (version !== "1.4.1") throw new Error("The Hydrogen adapter requires AIStudioToAPI v1.4.1.");

const RequestHandler = require(path.join(root, "src/core/RequestHandler"));
const FormatConverter = require(path.join(root, "src/core/FormatConverter"));
const { firefox } = require(path.join(root, "node_modules/playwright"));
const auditFile = path.join(root, "data/hydrogen-upstream-audit.jsonl");

function audit(record) {
  const entry = { time: new Date().toISOString(), ...record };
  try {
    fs.appendFileSync(auditFile, JSON.stringify(entry) + "\n", { mode: 0o600 });
  } catch {
    console.warn("[AIProxyKit] Cannot write the Hydrogen model audit.");
  }
  console.info("[AIProxyKit] " + JSON.stringify(entry));
}

const processOpenAIRequest = RequestHandler.prototype.processOpenAIRequest;
RequestHandler.prototype.processOpenAIRequest = function (req, res) {
  try {
    requireModel(req.body?.model);
  } catch {
    return res.status(400).json({ error: { type: "invalid_request_error", param: "model",
      code: "model_not_supported", message: "Use the exact gemini-4-hydrogen model ID." } });
  }
  return processOpenAIRequest.call(this, req, res);
};

for (const name of ["processOpenAIResponseRequest", "processOpenAIResponseInputTokens"]) {
  if (typeof RequestHandler.prototype[name] !== "function") throw new Error("Unsupported upstream handler layout.");
  RequestHandler.prototype[name] = function (_req, res) {
    return res.status(404).json({ error: { type: "invalid_request_error", code: "unsupported_endpoint",
      message: "This deployment supports /v1/chat/completions; Responses API is disabled." } });
  };
}

const prepare = RequestHandler.prototype._prepareGenerationProxyRequest;
RequestHandler.prototype._prepareGenerationProxyRequest = function (options) {
  requireModel(options.model);
  return prepare.call(this, options);
};

const forward = RequestHandler.prototype._forwardRequest;
RequestHandler.prototype._forwardRequest = function (request, ...args) {
  if (request.is_generative) {
    const model = generationModel(request.path);
    requireModel(model);
    audit({ event: "proxy_dispatch", request_id: request.request_id, model,
      path: new URL(request.path, "https://generativelanguage.googleapis.com").pathname });
  }
  return forward.call(this, request, ...args);
};

for (const method of ["convertGoogleToOpenAINonStream", "translateGoogleToOpenAIStream"]) {
  const convert = FormatConverter.prototype[method];
  if (typeof convert !== "function") throw new Error("Unsupported upstream converter layout.");
  FormatConverter.prototype[method] = function (response, model, ...args) {
    requireModel(model);
    if (response?.modelVersion !== undefined) {
      const observed = requireObservedModel(response.modelVersion);
      if (observed) audit({ event: "google_response_model", model: observed });
    }
    return convert.call(this, response, model, ...args);
  };
}

function observeContext(context) {
  context.on("response", response => {
    const url = new URL(response.url());
    const model = generationModel(url.href);
    if (!model || !/(^|\.)googleapis\.com$/.test(url.hostname)) return;
    audit({ event: "google_network_response", model, host: url.hostname,
      path: url.pathname, status: response.status() });
  });
  return context;
}

const launch = firefox.launch.bind(firefox);
firefox.launch = async function (...args) {
  const browser = await launch(...args);
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async function (...contextArgs) {
    return observeContext(await newContext(...contextArgs));
  };
  return browser;
};

audit({ event: "adapter_ready", model: MODEL, upstream_version: version });
