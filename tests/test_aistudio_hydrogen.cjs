"use strict";

const assert = require("node:assert/strict");
const { MODEL, requireModel, generationModel, requireObservedModel } = require("../services/aistudio-hydrogen/contract.cjs");
const registry = require("../services/aistudio-hydrogen/models.json");

assert.deepEqual(registry.models.map(model => model.name), ["models/" + MODEL]);
assert.equal(requireModel(MODEL), MODEL);
for (const model of [undefined, "", "models/" + MODEL, "Gemini-4-hydrogen", "gemini-4-hydrogen-search", "gemini-4-carbon"]) {
  assert.throws(() => requireModel(model), /exact gemini-4-hydrogen/);
}
assert.equal(generationModel("/v1beta/models/gemini-4-hydrogen:generateContent"), MODEL);
assert.equal(generationModel("https://generativelanguage.googleapis.com/v1beta/models/gemini-4-hydrogen:streamGenerateContent?alt=sse"), MODEL);
assert.equal(generationModel("/v1beta/models/gemini-4-carbon:generateContent"), "gemini-4-carbon");
assert.equal(generationModel("/v1beta/models"), null);
assert.equal(generationModel("/v1/chat/completions"), null);
assert.equal(requireObservedModel(undefined), null);
assert.equal(requireObservedModel(MODEL), MODEL);
assert.equal(requireObservedModel("models/" + MODEL + "-001"), MODEL + "-001");
assert.throws(() => requireObservedModel("gemini-4-carbon"), /different model/);
assert.throws(() => requireObservedModel("gemini-3.8-flash"), /different model/);
console.log("Hydrogen registry, exact model ID, Google path and observed model checks PASS");
