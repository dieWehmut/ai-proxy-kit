"use strict";

const MODEL = "gemini-4-hydrogen";

function requireModel(model) {
  if (model !== MODEL) {
    throw new Error("This deployment requires the exact gemini-4-hydrogen model ID.");
  }
  return MODEL;
}

function generationModel(url) {
  let path;
  try {
    path = new URL(url, "https://generativelanguage.googleapis.com").pathname;
  } catch {
    return null;
  }
  const match = path.match(/^\/v1(?:beta|alpha)?\/models\/([a-z0-9.-]+):(streamGenerateContent|generateContent)$/);
  return match ? match[1] : null;
}

function requireObservedModel(version) {
  if (typeof version !== "string") return null;
  const normalized = version.replace(/^models\//, "");
  if (normalized !== MODEL && !/^gemini-4-hydrogen(?:-[a-z0-9.]+)+$/.test(normalized)) {
    throw new Error("Google returned a different model; generation verification failed.");
  }
  return normalized;
}

module.exports = { MODEL, requireModel, generationModel, requireObservedModel };
