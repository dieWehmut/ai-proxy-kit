import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export class DiscoveryError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'DiscoveryError';
    this.code = code;
  }
}

function positiveInteger(value, fallback) {
  return Number.isSafeInteger(value) && value > 0 ? value : fallback;
}

export function keyFingerprint(key) {
  return createHash('sha256').update(key).digest('hex');
}

export function metadataUrl(baseUrl) {
  return baseUrl.replace(/\/+$/, '') + '/sub2api/key-group';
}

/** Reject ambiguous groups; aliases of the same target are not extra models. */
export function normalizeMetadata(payload, preferredAlias) {
  const data = payload?.data ?? payload;
  if (data?.schema_version !== 1 || data?.object !== 'sub2api.key_group') {
    throw new DiscoveryError('invalid-schema', 'Key group metadata has an unsupported schema');
  }
  const group = data.group;
  if (!group || !positiveInteger(group.id, 0) ||
      typeof group.name !== 'string' || !group.name.trim() ||
      typeof group.platform !== 'string' || !group.platform.trim()) {
    throw new DiscoveryError('missing-group', 'The key has no complete group metadata');
  }
  if (group.status !== 'active') {
    throw new DiscoveryError('inactive-group', 'The key group is not active');
  }
  if (!positiveInteger(data.key?.id, 0) || data.key?.group_id !== group.id) {
    throw new DiscoveryError('inconsistent-group', 'Key and group identities do not match');
  }
  if (!Array.isArray(data.models) || data.models.length !== 1) {
    throw new DiscoveryError('non-unique-model', 'The key group must have exactly one actual model');
  }
  const model = data.models[0];
  const aliases = Array.isArray(model?.aliases) ? [...new Set(model.aliases)] : [];
  if (typeof model?.id !== 'string' || !model.id.trim() ||
      typeof model.upstream_model !== 'string' || !model.upstream_model.trim() ||
      !aliases.includes(model.id) ||
      aliases.some((alias) => typeof alias !== 'string' || !alias.trim())) {
    throw new DiscoveryError('invalid-model', 'The group model has invalid identity or aliases');
  }
  // Retaining a callable alias does not choose another actual target.
  const id = aliases.includes(preferredAlias) ? preferredAlias : model.id;
  return {
    schema_version: 1,
    key_id: data.key.id,
    group: { id: group.id, name: group.name, platform: group.platform, status: group.status },
    model: { ...model, id, aliases },
    revision: String(data.revision ?? ''),
  };
}

export function transportFor(platform, baseUrl) {
  if (platform === 'openai' || platform === 'deepseek') {
    return { api: 'openai-completions', baseUrl };
  }
  if (platform === 'anthropic') {
    return { api: 'anthropic-messages', baseUrl };
  }
  if (platform === 'gemini') {
    return { api: 'google-generative-ai', baseUrl: baseUrl.replace(/\/v1\/?$/, '') };
  }
  throw new DiscoveryError('unsupported-platform', 'The group platform has no supported OpenClaw transport');
}

export function runtimeModel(snapshot, baseUrl) {
  const model = snapshot.model;
  const deepseek = snapshot.group.platform === 'deepseek';
  return {
    id: model.id,
    name: model.id,
    provider: 'sub2api',
    ...transportFor(snapshot.group.platform, baseUrl),
    reasoning: typeof model.reasoning === 'boolean' ? model.reasoning : deepseek,
    input: model.input ?? (deepseek ? ['text', 'image'] : ['text']),
    contextWindow: positiveInteger(model.context_window, deepseek ? 1000000 : 128000),
    maxTokens: positiveInteger(model.max_tokens, deepseek ? 32768 : 8192),
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: deepseek ? {
      supportsReasoningEffort: true,
      thinkingFormat: 'deepseek',
      supportedReasoningEfforts: ['low', 'high', 'xhigh'],
      reasoningEffortMap: { xhigh: 'max' },
      supportsUsageInStreaming: true,
    } : { supportsUsageInStreaming: true },
  };
}

/** Atomic replacement plus a compare-before-replace check protects user edits. */
export function privateWrite(path, value, expected) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temp = path + '.tmp-' + randomUUID();
  try {
    writeFileSync(temp, value, { mode: 0o600, flag: 'wx' });
    if (expected !== undefined && readFileSync(path, 'utf8') !== expected) {
      throw new DiscoveryError('config-changed', 'OpenClaw configuration changed during synchronization');
    }
    renameSync(temp, path);
  } finally {
    if (existsSync(temp)) unlinkSync(temp);
  }
}

export function updateConfig(config, snapshot, baseUrl) {
  const next = structuredClone(config);
  const provider = next.models?.providers?.sub2api;
  if (!provider) throw new DiscoveryError('unmanaged-config', 'The Sub2API provider is not configured');
  const definition = runtimeModel(snapshot, baseUrl);
  const { provider: unusedProvider, baseUrl: inferenceBase, api, ...entry } = definition;
  provider.baseUrl = inferenceBase;
  provider.api = api;
  provider.models = [entry];
  const defaults = next.agents?.defaults;
  if (!defaults) throw new DiscoveryError('unmanaged-config', 'OpenClaw agent defaults are missing');
  const primary = typeof defaults.model === 'string' ? defaults.model : defaults.model?.primary;
  if (primary?.startsWith('sub2api/')) {
    const ref = 'sub2api/' + snapshot.model.id;
    defaults.model = { ...((typeof defaults.model === 'object' && defaults.model) || {}), primary: ref, fallbacks: [] };
    defaults.models ??= {};
    for (const id of Object.keys(defaults.models)) {
      if (id.startsWith('sub2api/')) delete defaults.models[id];
    }
    defaults.models[ref] = { alias: snapshot.model.id };
    if (defaults.modelPolicy?.allow) {
      defaults.modelPolicy.allow = [
        ...defaults.modelPolicy.allow.filter((id) => !id.startsWith('sub2api/')),
        'sub2api/*',
      ];
    }
  }
  return next;
}

export function writeOpenClawConfig(path, snapshot, baseUrl) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const previous = readFileSync(path, 'utf8');
    const next = updateConfig(JSON.parse(previous), snapshot, baseUrl);
    const text = JSON.stringify(next, null, 2) + '\n';
    if (JSON.stringify(JSON.parse(previous)) === JSON.stringify(next)) return false;
    try {
      privateWrite(path, text, previous);
      return true;
    } catch (error) {
      if (error.code !== 'config-changed' || attempt === 2) throw error;
    }
  }
}

/** One key-scoped interface for network discovery, persistence and fallback. */
export class KeyGroupClient {
  constructor({ baseUrl, key, statePath, fetchImpl = fetch, logger = console,
    timeoutMs = 4000, now = () => Date.now(), preferredAlias, onSnapshot = () => {} }) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.key = key;
    this.fingerprint = keyFingerprint(key);
    this.statePath = statePath;
    this.fetchImpl = fetchImpl;
    this.logger = logger;
    this.timeoutMs = timeoutMs;
    this.now = now;
    this.preferredAlias = preferredAlias;
    this.onSnapshot = onSnapshot;
    this.current = undefined;
    this.inFlight = undefined;
    this.lastError = undefined;
    this.lastWarning = 0;
    this.readState();
  }

  readState() {
    if (!this.statePath || !existsSync(this.statePath)) return;
    try {
      const state = JSON.parse(readFileSync(this.statePath, 'utf8'));
      if (state.fingerprint !== this.fingerprint || state.base_url !== this.baseUrl) return;
      const raw = {
        object: 'sub2api.key_group', schema_version: 1,
        key: { id: state.key_id, group_id: state.group?.id },
        group: state.group, models: [state.model], revision: state.revision,
      };
      this.current = { ...normalizeMetadata(raw, state.model?.id), checked_at: state.checked_at };
    } catch {
      // A malformed cache never prevents a fresh discovery or leaks credentials.
      this.current = undefined;
    }
  }

  writeState() {
    if (!this.statePath || !this.current) return;
    privateWrite(this.statePath, JSON.stringify({
      ...this.current, fingerprint: this.fingerprint, base_url: this.baseUrl,
    }, null, 2) + '\n');
  }

  async refresh({ fresh = false } = {}) {
    // A user turn must not adopt a background request started before a group edit.
    while (this.inFlight) {
      const pending = this.inFlight;
      await pending;
      if (!fresh) return this.current;
    }
    this.inFlight = this.discover();
    try { return await this.inFlight; }
    finally { this.inFlight = undefined; }
  }

  async discover() {
    try {
      if (!this.key) throw new DiscoveryError('missing-key', 'Sub2API API key is not configured');
      const response = await this.fetchImpl(metadataUrl(this.baseUrl), {
        headers: { Authorization: 'Bearer ' + this.key, Accept: 'application/json' },
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      if (!response.ok) throw new DiscoveryError('http-' + response.status, 'Key group discovery returned HTTP ' + response.status);
      const snapshot = normalizeMetadata(await response.json(), this.current?.model.id ?? this.preferredAlias);
      transportFor(snapshot.group.platform, this.baseUrl);
      snapshot.checked_at = new Date(this.now()).toISOString();
      await this.onSnapshot(snapshot);
      this.current = snapshot;
      this.lastError = undefined;
      this.writeState();
      return this.current;
    } catch (error) {
      // Do not include fetch exception text, URLs or server bodies in diagnostics.
      const code = error instanceof DiscoveryError ? error.code : 'discovery-unavailable';
      this.lastError = { code, at: new Date(this.now()).toISOString(), keeping_last_model: Boolean(this.current) };
      if (this.now() - this.lastWarning >= 30000 || !this.lastWarning) {
        this.logger.warn?.('Sub2API group sync: ' + code + (this.current ? '; keeping last available model' : '; no cached model'));
        this.lastWarning = this.now();
      }
      return this.current;
    }
  }
}
