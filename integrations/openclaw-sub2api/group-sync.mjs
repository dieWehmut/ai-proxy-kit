import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
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

/** Stock Sub2API exposes the key group's allowlist through the standard list endpoint. */
export function modelsUrl(baseUrl) {
  return baseUrl.replace(/\/+$/, '') + '/models';
}

// A Sub2API build without the key-group patch answers 404/405/501 here; the
// OpenAI-compatible model list is then the only key-scoped source available.
const MODELS_FALLBACK_STATUS = new Set([404, 405, 501]);
const MODELS_GROUP_NAME = 'key-models';

/**
 * Interpret an OpenAI-compatible model list as a key-group snapshot.
 * Only an unambiguous single model may be adopted; aliases of one target are
 * deduplicated so a repeated id never looks like a second model.
 */
export function normalizeModelsList(payload, preferredAlias) {
  const data = payload?.data ?? payload;
  const ids = [];
  if (Array.isArray(data)) {
    for (const entry of data) {
      const id = typeof entry?.id === 'string' ? entry.id.trim() : '';
      if (id && !ids.includes(id)) ids.push(id);
    }
  }
  if (ids.length === 0) {
    throw new DiscoveryError('empty-model-list', 'The key currently exposes no models');
  }
  if (ids.length !== 1) {
    throw new DiscoveryError('non-unique-model', 'The key must expose exactly one model');
  }
  const id = preferredAlias && ids.includes(preferredAlias) ? preferredAlias : ids[0];
  return { model: { id, upstream_model: ids[0], aliases: ids } };
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
    maxTokens: positiveInteger(model.max_tokens, deepseek ? 128000 : 8192),
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
export function privateWrite(path, value, expected, assertCurrent = () => {}) {
  assertCurrent();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temp = path + '.tmp-' + randomUUID();
  try {
    writeFileSync(temp, value, { mode: 0o600, flag: 'wx' });
    assertCurrent();
    if (expected !== undefined && readFileSync(path, 'utf8') !== expected) {
      throw new DiscoveryError('config-changed', 'OpenClaw configuration changed during synchronization');
    }
    assertCurrent();
    renameSync(temp, path);
  } finally {
    if (existsSync(temp)) unlinkSync(temp);
  }
}

// Python is already installed on the Linux OpenClaw hosts. Kernel flock releases
// on process/pipe closure, so crashed owners never leave a guessed stale lock.
const LOCK_PROGRAM = [
  'import fcntl, os, sys',
  'path = sys.argv[1]',
  'os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)',
  'fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)',
  'with os.fdopen(fd, "a+") as lock:',
  '    fcntl.flock(lock, fcntl.LOCK_EX)',
  '    sys.stdout.write("LOCKED\\n")',
  '    sys.stdout.flush()',
  '    sys.stdin.buffer.read()',
].join('\n');

export async function withFileLock(path, operation) {
  const child = spawn('python3', ['-c', LOCK_PROGRAM, path], { stdio: ['pipe', 'pipe', 'ignore'] });
  child.stdin.on('error', () => {});
  const closed = new Promise((resolve) => child.once('close', resolve));
  let timeout;
  let lost = false;
  let identity;
  const processIdentity = () => {
    const raw = readFileSync('/proc/' + child.pid + '/stat', 'utf8');
    const fields = raw.slice(raw.lastIndexOf(')') + 2).split(' ');
    if (fields[0] === 'Z' || fields[0] === 'X' || Number(fields[1]) !== process.pid) {
      throw new Error('Lock worker is not a live child');
    }
    return fields[19];
  };
  const assertCurrent = () => {
    try {
      if (lost || child.exitCode !== null || !identity || processIdentity() !== identity) throw new Error('Lock worker exited');
    } catch {
      throw new DiscoveryError('coordination-lost', 'The model state lock is no longer held');
    }
  };
  assertCurrent.workerPid = child.pid;
  const acquired = new Promise((resolve, reject) => {
    let output = '';
    let ready = false;
    child.stdout.on('data', (chunk) => {
      output += chunk.toString();
      if (output.includes('LOCKED\n') && !ready) { ready = true; resolve(); }
    });
    child.once('error', () => reject(new DiscoveryError('coordination-unavailable', 'Model state coordination is unavailable')));
    child.once('close', () => {
      lost = true;
      if (!ready) reject(new DiscoveryError('coordination-unavailable', 'Model state coordination exited before acquiring its lock'));
    });
    timeout = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new DiscoveryError('coordination-timeout', 'Timed out coordinating model state'));
    }, 10000);
  });
  try {
    await acquired;
    clearTimeout(timeout);
    try { identity = processIdentity(); } catch { assertCurrent(); }
    assertCurrent();
    const result = await operation(assertCurrent);
    assertCurrent();
    return result;
  } finally {
    clearTimeout(timeout);
    child.stdin.end();
    await closed;
  }
}

export function updateConfig(config, snapshot, baseUrl, previousSnapshot) {
  const next = structuredClone(config);
  const provider = next.models?.providers?.sub2api;
  if (!provider) throw new DiscoveryError('unmanaged-config', 'The Sub2API provider is not configured');
  const definition = runtimeModel(snapshot, baseUrl);
  const { provider: unusedProvider, baseUrl: inferenceBase, api, ...entry } = definition;
  const sameTarget = !previousSnapshot ||
    (previousSnapshot.model.upstream_model === snapshot.model.upstream_model &&
      previousSnapshot.group.platform === snapshot.group.platform);
  // Keep any richer metadata already published for the same model id, even when
  // the discovery tier changed the reported platform (key-group -> model list).
  const oldEntry = provider.models?.find((model) => model.id === snapshot.model.id) ??
    (sameTarget ? provider.models?.find((model) => model.id === previousSnapshot?.model.id) : undefined);
  provider.baseUrl = inferenceBase;
  provider.api = api;
  provider.models = [{ ...(oldEntry || entry), id: entry.id, api }];
  const defaults = next.agents?.defaults;
  if (!defaults) throw new DiscoveryError('unmanaged-config', 'OpenClaw agent defaults are missing');
  const primary = typeof defaults.model === 'string' ? defaults.model : defaults.model?.primary;
  if (primary?.startsWith('sub2api/')) {
    const ref = 'sub2api/' + snapshot.model.id;
    defaults.model = { ...((typeof defaults.model === 'object' && defaults.model) || {}), primary: ref, fallbacks: [] };
    defaults.models ??= {};
    const oldRef = previousSnapshot?.model.id && 'sub2api/' + previousSnapshot.model.id;
    const selectedOptions = defaults.models[ref] ?? (sameTarget && oldRef ? defaults.models[oldRef] : undefined);
    for (const id of Object.keys(defaults.models)) {
      if (id.startsWith('sub2api/') && id !== 'sub2api/*') delete defaults.models[id];
    }
    defaults.models[ref] = selectedOptions ?? { alias: snapshot.model.id };
    if (defaults.modelPolicy?.allow) {
      defaults.modelPolicy.allow = [
        ...defaults.modelPolicy.allow.filter((id) => !id.startsWith('sub2api/')),
        'sub2api/*',
      ];
    }
  }
  return next;
}

export function writeOpenClawConfig(path, snapshot, baseUrl, previousSnapshot, assertCurrent = () => {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const previous = readFileSync(path, 'utf8');
    const next = updateConfig(JSON.parse(previous), snapshot, baseUrl, previousSnapshot);
    const text = JSON.stringify(next, null, 2) + '\n';
    if (JSON.stringify(JSON.parse(previous)) === JSON.stringify(next)) return false;
    try {
      privateWrite(path, text, previous, assertCurrent);
      return true;
    } catch (error) {
      if (error.code !== 'config-changed' || attempt === 2) throw error;
    }
  }
}

/** One key-scoped interface for network discovery, persistence and fallback. */
export class KeyGroupClient {
  constructor({ baseUrl, key, statePath, configPath, fetchImpl = fetch, logger = console,
    timeoutMs = 4000, now = () => Date.now(), preferredAlias, onSnapshot = () => {} }) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.key = key;
    this.fingerprint = keyFingerprint(key);
    this.statePath = statePath;
    this.configPath = configPath;
    this.fetchImpl = fetchImpl;
    this.logger = logger;
    this.timeoutMs = timeoutMs;
    this.now = now;
    this.preferredAlias = preferredAlias;
    this.onSnapshot = onSnapshot;
    this.current = undefined;
    this.inFlight = undefined;
    this.lastError = undefined;
    // Set when discovery authoritatively reports more than one candidate model.
    // The cache is kept for diagnostics, but no turn may be overridden.
    this.ambiguous = false;
    this.fallbackNoted = false;
    this.lastWarnCode = undefined;
    this.lastWarning = 0;
    this.generation = 0;
    this.enrolledGeneration = undefined;
    this.superseded = false;
    this.readState();
  }

  readState() {
    if (!this.statePath || !existsSync(this.statePath)) return;
    try {
      const persisted = this.effectiveStateRaw();
      if (persisted === undefined) return;
      const state = JSON.parse(persisted);
      if (state.fingerprint !== this.fingerprint || state.base_url !== this.baseUrl) return;
      const raw = {
        object: 'sub2api.key_group', schema_version: 1,
        key: { id: state.key_id, group_id: state.group?.id },
        group: state.group, models: [state.model], revision: state.revision,
      };
      this.current = { ...normalizeMetadata(raw, state.model?.id), checked_at: state.checked_at,
        generation: positiveInteger(state.generation, 0),
        identity_generation: positiveInteger(state.identity_generation, 0) };
    } catch {
      // A malformed cache never prevents a fresh discovery or leaks credentials.
      // Preserve a known in-memory fallback when a later cache read is damaged.
    }
  }

  stateText(snapshot) {
    return JSON.stringify({ ...snapshot, fingerprint: this.fingerprint, base_url: this.baseUrl }, null, 2) + '\n';
  }

  readOptional(path) {
    return path && existsSync(path) ? readFileSync(path, 'utf8') : undefined;
  }

  transactionPath() { return this.statePath + '.transaction.json'; }

  effectiveStateRaw() {
    const current = this.readOptional(this.statePath);
    if (!this.configPath || !existsSync(this.transactionPath())) return current;
    const pending = JSON.parse(readFileSync(this.transactionPath(), 'utf8'));
    if (pending.config_path !== this.configPath || pending.state_path !== this.statePath) return current;
    const newer = current && positiveInteger(JSON.parse(current).generation, 0) > pending.generation;
    const complete = current === pending.state_after && this.readOptional(this.configPath) === pending.config_after;
    return newer || complete ? current : pending.state_before;
  }

  sharedHeader() {
    try { return JSON.parse(this.effectiveStateRaw() ?? '{}'); } catch { return {}; }
  }

  recoverTransaction(assertCurrent) {
    if (!this.configPath || !existsSync(this.transactionPath())) return;
    assertCurrent();
    const pending = JSON.parse(readFileSync(this.transactionPath(), 'utf8'));
    if (pending.config_path !== this.configPath || pending.state_path !== this.statePath) {
      throw new DiscoveryError('persistence-conflict', 'A pending model transaction belongs to another profile');
    }
    const state = this.readOptional(this.statePath);
    const config = this.readOptional(this.configPath);
    const newer = state && positiveInteger(JSON.parse(state).generation, 0) > pending.generation;
    const complete = state === pending.state_after && config === pending.config_after;
    if (!newer && !complete) {
      for (const [path, before, after, current] of [
        [this.statePath, pending.state_before, pending.state_after, state],
        [this.configPath, pending.config_before, pending.config_after, config],
      ]) {
        if (current !== before && current !== after) {
          throw new DiscoveryError('persistence-conflict', 'A user edit prevents automatic transaction recovery');
        }
        if (current !== before) {
          assertCurrent();
          if (before === undefined) unlinkSync(path);
          else privateWrite(path, before, current, assertCurrent);
        }
      }
    }
    assertCurrent();
    unlinkSync(this.transactionPath());
  }

  writeState(assertCurrent = () => {}, expected) {
    if (!this.statePath || !this.current) return;
    privateWrite(this.statePath, this.stateText(this.current), expected, assertCurrent);
  }

  writeConfig(value, expected, assertCurrent) {
    privateWrite(this.configPath, value, expected, assertCurrent);
  }

  async beginRequest() {
    if (this.superseded) throw new DiscoveryError('superseded-identity', 'This model synchronizer belongs to an older profile identity');
    if (!this.statePath) return ++this.generation;
    return await withFileLock(this.statePath + '.lock', (assertCurrent) => {
      this.recoverTransaction(assertCurrent);
      const path = this.statePath + '.sequence.json';
      let sequence = 0;
      try { sequence = positiveInteger(JSON.parse(readFileSync(path, 'utf8')).generation, 0); } catch {}
      this.readState();
      const shared = this.sharedHeader();
      const sameIdentity = shared.fingerprint === this.fingerprint && shared.base_url === this.baseUrl;
      const identityGeneration = positiveInteger(shared.identity_generation, positiveInteger(shared.generation, 0));
      if (this.enrolledGeneration !== undefined && !sameIdentity && identityGeneration > this.enrolledGeneration) {
        this.superseded = true;
        throw new DiscoveryError('superseded-identity', 'This model synchronizer belongs to an older profile identity');
      }
      const generation = Math.max(sequence, positiveInteger(shared.generation, 0), this.current?.generation ?? 0) + 1;
      this.enrolledGeneration ??= generation;
      privateWrite(path, JSON.stringify({ generation }) + '\n', undefined, assertCurrent);
      return generation;
    });
  }

  async commit(snapshot, generation) {
    const apply = async (assertCurrent = () => {}) => {
      this.recoverTransaction(assertCurrent);
      const shared = this.sharedHeader();
      const sameIdentity = shared.fingerprint === this.fingerprint && shared.base_url === this.baseUrl;
      const identityGeneration = positiveInteger(shared.identity_generation, positiveInteger(shared.generation, 0));
      if (!sameIdentity && identityGeneration > (this.enrolledGeneration ?? generation)) {
        this.superseded = true;
      }
      // Every process rereads the same committed state while holding the lock.
      if (this.statePath) this.readState();
      if (positiveInteger(shared.generation, 0) > generation || this.superseded) return this.current;
      const previous = this.current;
      const stateBefore = this.readOptional(this.statePath);
      const next = { ...snapshot, generation,
        identity_generation: sameIdentity ? (identityGeneration || this.enrolledGeneration || generation) : (this.enrolledGeneration ?? generation) };
      let configBefore;
      let configAfter;
      if (this.configPath) {
        configBefore = readFileSync(this.configPath, 'utf8');
        const config = JSON.parse(configBefore);
        if (config.plugins?.entries?.['sub2api-auto']?.enabled === false) return previous;
        const updated = updateConfig(config, next, this.baseUrl, previous);
        configAfter = JSON.stringify(config) === JSON.stringify(updated) ? configBefore : JSON.stringify(updated, null, 2) + '\n';
        privateWrite(this.transactionPath(), JSON.stringify({
          generation, state_path: this.statePath, config_path: this.configPath,
          state_before: stateBefore, state_after: this.stateText(next), config_before: configBefore, config_after: configAfter,
        }, null, 2) + '\n', undefined, assertCurrent);
      }
      try {
        // Cache persistence must succeed before publishing a new configuration.
        this.current = next;
        this.writeState(assertCurrent, stateBefore);
        assertCurrent();
        if (this.configPath) {
          if (configAfter !== configBefore) this.writeConfig(configAfter, configBefore, assertCurrent);
          assertCurrent();
          unlinkSync(this.transactionPath());
        } else {
          // Legacy callbacks must be atomic and use the supplied lock guard.
          await this.onSnapshot(snapshot, previous, assertCurrent);
          assertCurrent();
        }
        this.lastError = undefined;
        return this.current;
      } catch (error) {
        this.current = previous;
        try {
          if (this.configPath) this.recoverTransaction(assertCurrent);
          else if (this.statePath) {
            assertCurrent();
            const current = this.readOptional(this.statePath);
            if (current === this.stateText(next)) {
              if (stateBefore === undefined) unlinkSync(this.statePath);
              else privateWrite(this.statePath, stateBefore, current, assertCurrent);
            }
          }
          this.readState();
        } catch { /* Keep the private recovery journal if storage or lock ownership is unavailable. */ }
        throw error instanceof DiscoveryError ? error : new DiscoveryError('persistence-unavailable', 'Persisted model synchronization failed');
      }
    };
    return this.statePath ? await withFileLock(this.statePath + '.lock', apply) : await apply();
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

  async request(url) {
    const response = await this.fetchImpl(url, {
      headers: { Authorization: 'Bearer ' + this.key, Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    return { ok: response.ok, status: response.status,
      body: response.ok ? await response.json() : undefined };
  }

  /** A synthetic, credential-free snapshot for the model-list fallback tier. */
  modelsSnapshot(listed) {
    const identity = positiveInteger(this.current?.key_id, 1);
    // /models carries no platform. Reuse the last authoritative one so a single
    // 404 during a deploy blip cannot rewrite a working anthropic/gemini/deepseek
    // deployment to the openai-completions transport (and persist that guess).
    const platform = this.current?.group?.platform ?? 'openai';
    return {
      schema_version: 1,
      key_id: identity,
      group: { id: identity, name: MODELS_GROUP_NAME, platform, status: 'active' },
      model: listed.model,
      revision: 'models:' + keyFingerprint(listed.model.aliases.join('\n')),
    };
  }

  async discover() {
    try {
      if (!this.key) throw new DiscoveryError('missing-key', 'Sub2API API key is not configured');
      const generation = await this.beginRequest();
      const alias = this.current?.model.id ?? this.preferredAlias;
      const metadata = await this.request(metadataUrl(this.baseUrl));
      let snapshot;
      if (metadata.ok) {
        snapshot = normalizeMetadata(metadata.body, alias);
      } else if (MODELS_FALLBACK_STATUS.has(metadata.status)) {
        const listed = await this.request(modelsUrl(this.baseUrl));
        if (!listed.ok) {
          throw new DiscoveryError('http-' + listed.status, 'Model list discovery returned HTTP ' + listed.status);
        }
        if (!this.fallbackNoted) {
          this.fallbackNoted = true;
          this.logger.info?.('Sub2API group sync: key-group metadata is unavailable; using the key-scoped model list');
        }
        snapshot = this.modelsSnapshot(normalizeModelsList(listed.body, alias));
      } else {
        throw new DiscoveryError('http-' + metadata.status, 'Key group discovery returned HTTP ' + metadata.status);
      }
      transportFor(snapshot.group.platform, this.baseUrl);
      this.ambiguous = false;
      this.fallbackNoted = false;
      snapshot.checked_at = new Date(this.now()).toISOString();
      return await this.commit(snapshot, generation);
    } catch (error) {
      // Do not include fetch exception text, URLs or server bodies in diagnostics.
      const code = error instanceof DiscoveryError ? error.code : 'discovery-unavailable';
      // An authoritative ambiguity is not a transient failure: holding the old
      // model would pin the bot to a target the key may no longer serve.
      if (code === 'non-unique-model') this.ambiguous = true;
      this.lastError = { code, at: new Date(this.now()).toISOString(), keeping_last_model: Boolean(this.current) };
      // A permanently ambiguous key is a stable state, not an incident: warn once
      // an hour instead of every refresh interval. A different code always warns.
      const interval = code === 'non-unique-model' ? 3600000 : 30000;
      if (code !== this.lastWarnCode || this.now() - this.lastWarning >= interval || !this.lastWarning) {
        try {
          this.logger.warn?.('Sub2API group sync: ' + code + (this.current ? '; keeping last available model' : '; no cached model'));
        } catch { /* an untrusted logger must never fail a turn */ }
        this.lastWarning = this.now();
        this.lastWarnCode = code;
      }
      return this.current;
    }
  }
}
