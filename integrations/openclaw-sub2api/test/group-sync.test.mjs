import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DiscoveryError, KeyGroupClient, normalizeMetadata, runtimeModel,
  updateConfig, writeOpenClawConfig } from '../group-sync.mjs';

const fixture = (id = 'deepseek-v4.1-flash', target = 'cn:deepseek-v4.1-flash', groupId = 8) => ({
  object: 'sub2api.key_group', schema_version: 1,
  key: { id: 8, group_id: groupId },
  group: { id: groupId, name: 'bot-group', platform: 'deepseek', status: 'active' },
  models: [{ id, upstream_model: target, aliases: [id, target] }], revision: id + '-' + groupId,
});

const config = () => ({
  models: { mode: 'replace', providers: { sub2api: {
    baseUrl: 'http://sub2api/v1', api: 'openai-completions',
    apiKey: { source: 'env', provider: 'default', id: 'SUB2API_API_KEY' },
    models: [{ id: 'old-model', name: 'old-model' }],
  } } },
  agents: { defaults: {
    model: { primary: 'sub2api/old-model', fallbacks: ['sub2api/old-fallback'] },
    models: { 'sub2api/old-model': { alias: 'old' }, 'other/untouched': { alias: 'other' } },
    modelPolicy: { allow: ['sub2api/old-model', 'other/untouched'] },
    thinkingDefault: 'max', workspace: '/root/.openclaw/workspace',
  } },
  channels: { qq: { admins: 'fixture-admin', systemPrompt: 'Keep this prompt' } },
  mcp: { servers: { fixture: { command: 'fixture-command' } } },
});

function setup(t) {
  const dir = mkdtempSync(join(tmpdir(), 'sub2api-group-sync-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return { dir, statePath: join(dir, 'state.json'), configPath: join(dir, 'openclaw.json') };
}

test('aliases of the only actual model retain an existing callable name', () => {
  const data = fixture('cn:deepseek-v4.1-flash');
  data.models[0].aliases.push('deepseek-v4.1-flash', 'deepseek-flash');
  const snapshot = normalizeMetadata(data, 'deepseek-v4.1-flash');
  assert.equal(snapshot.model.id, 'deepseek-v4.1-flash');
  assert.equal(snapshot.model.upstream_model, 'cn:deepseek-v4.1-flash');
  assert.deepEqual(snapshot.group, data.group);
});

test('a genuinely different target adopts its canonical callable model', () => {
  const snapshot = normalizeMetadata(fixture('different-model', 'cn:different-model', 9), 'deepseek-v4.1-flash');
  assert.equal(snapshot.model.id, 'different-model');
  assert.equal(snapshot.model.upstream_model, 'cn:different-model');
  assert.equal(snapshot.group.id, 9);
});

test('empty, multiple actual models and inconsistent groups are rejected', () => {
  const empty = fixture(); empty.models = [];
  assert.throws(() => normalizeMetadata(empty), (error) => error.code === 'non-unique-model');
  const multiple = fixture(); multiple.models.push(fixture('second', 'cn:second').models[0]);
  assert.throws(() => normalizeMetadata(multiple), (error) => error.code === 'non-unique-model');
  const mismatch = fixture(); mismatch.key.group_id = 999;
  assert.throws(() => normalizeMetadata(mismatch), (error) => error.code === 'inconsistent-group');
  const inactive = fixture(); inactive.group.status = 'disabled';
  assert.throws(() => normalizeMetadata(inactive), (error) => error.code === 'inactive-group');
});

test('managed model updates preserve QQ, MCP and reasoning preferences', () => {
  const old = config(); const snapshot = normalizeMetadata(fixture());
  const next = updateConfig(old, snapshot, 'http://sub2api/v1');
  assert.equal(next.agents.defaults.model.primary, 'sub2api/deepseek-v4.1-flash');
  assert.deepEqual(next.agents.defaults.model.fallbacks, []);
  assert.deepEqual(next.agents.defaults.modelPolicy.allow, ['other/untouched', 'sub2api/*']);
  assert.equal(next.agents.defaults.thinkingDefault, 'max');
  assert.deepEqual(next.channels, old.channels);
  assert.deepEqual(next.mcp, old.mcp);
  assert.deepEqual(next.models.providers.sub2api.apiKey, old.models.providers.sub2api.apiKey);
  assert.deepEqual(next.agents.defaults.models['other/untouched'], { alias: 'other' });
  assert.equal(old.agents.defaults.model.primary, 'sub2api/old-model');
});

test('a non-Sub2API default is not forcibly changed by catalog updates', () => {
  const old = config(); old.agents.defaults.model.primary = 'other/untouched';
  const next = updateConfig(old, normalizeMetadata(fixture()), 'http://sub2api/v1');
  assert.deepEqual(next.agents.defaults, old.agents.defaults);
  assert.equal(next.models.providers.sub2api.models[0].id, 'deepseek-v4.1-flash');
});

test('platform transport changes without a hardcoded model identity', () => {
  const data = fixture('claude-fixture', 'claude-fixture'); data.group.platform = 'anthropic';
  const model = runtimeModel(normalizeMetadata(data), 'http://sub2api/v1');
  assert.equal(model.api, 'anthropic-messages');
  assert.equal(model.id, 'claude-fixture');
  const unsupported = fixture(); unsupported.group.platform = 'unsupported';
  assert.throws(() => runtimeModel(normalizeMetadata(unsupported), 'http://sub2api/v1'), DiscoveryError);
});

test('configuration is atomically written privately and repeated refresh is a no-op', (t) => {
  const { configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
  const snapshot = normalizeMetadata(fixture());
  assert.equal(writeOpenClawConfig(configPath, snapshot, 'http://sub2api/v1'), true);
  const stat = statSync(configPath);
  assert.equal(stat.mode & 0o777, 0o600);
  assert.equal(writeOpenClawConfig(configPath, snapshot, 'http://sub2api/v1'), false);
  assert.equal(statSync(configPath).ino, stat.ino);
});

test('discovery uses only its own key and stores no plaintext credential', async (t) => {
  const { statePath } = setup(t); let request;
  const client = new KeyGroupClient({ baseUrl: 'http://sub2api/v1/', key: 'fixture-private-key', statePath,
    fetchImpl: async (url, options) => { request = { url, options }; return { ok: true, json: async () => fixture() }; },
  });
  const snapshot = await client.refresh({ fresh: true });
  assert.equal(request.url, 'http://sub2api/v1/sub2api/key-group');
  assert.equal(request.options.headers.Authorization, 'Bearer fixture-private-key');
  assert.equal(snapshot.group.id, 8);
  const stored = readFileSync(statePath, 'utf8');
  assert.equal(stored.includes('fixture-private-key'), false);
  assert.equal(statSync(statePath).mode & 0o777, 0o600);
});

test('network error and empty group keep the last available model across restart', async (t) => {
  const { statePath } = setup(t); let mode = 'valid'; const warnings = [];
  const fetchImpl = async () => {
    if (mode === 'network') throw new Error('Do not leak Bearer fixture-private-key');
    const data = fixture(); if (mode === 'empty') data.models = [];
    return { ok: true, json: async () => data };
  };
  const options = { baseUrl: 'http://sub2api/v1', key: 'fixture-private-key', statePath, fetchImpl,
    logger: { warn: (message) => warnings.push(message) } };
  const client = new KeyGroupClient(options); await client.refresh();
  mode = 'network';
  assert.equal((await client.refresh({ fresh: true })).model.id, 'deepseek-v4.1-flash');
  assert.equal(client.lastError.code, 'discovery-unavailable');
  mode = 'empty';
  assert.equal((await client.refresh({ fresh: true })).model.id, 'deepseek-v4.1-flash');
  assert.equal(client.lastError.code, 'non-unique-model');
  const rebooted = new KeyGroupClient(options);
  assert.equal(rebooted.current.model.id, 'deepseek-v4.1-flash');
  assert.equal(warnings.join(' ').includes('fixture-private-key'), false);
  const anotherKey = new KeyGroupClient({ ...options, key: 'different-private-key' });
  assert.equal(anotherKey.current, undefined);
});

test('a turn sends a fresh request after a background request that captured old group state', async () => {
  let releaseBackground; let calls = 0;
  const backgroundResponse = new Promise((resolve) => { releaseBackground = resolve; });
  const client = new KeyGroupClient({ baseUrl: 'http://sub2api/v1', key: 'fixture-key',
    fetchImpl: async () => {
      calls++;
      if (calls === 1) return await backgroundResponse;
      return { ok: true, json: async () => fixture('new-model', 'cn:new-model', 9) };
    },
  });
  const background = client.refresh();
  const turn = client.refresh({ fresh: true });
  releaseBackground({ ok: true, json: async () => fixture() });
  assert.equal((await background).model.id, 'deepseek-v4.1-flash');
  assert.equal((await turn).model.id, 'new-model');
  assert.equal(calls, 2);
});

test('a failed atomic config update cannot replace the known usable snapshot', async () => {
  let fail = false;
  const client = new KeyGroupClient({ baseUrl: 'http://sub2api/v1', key: 'fixture-key', logger: { warn() {} },
    fetchImpl: async () => ({ ok: true, json: async () => fixture(fail ? 'new' : 'old', fail ? 'cn:new' : 'cn:old') }),
    onSnapshot: () => { if (fail) throw new DiscoveryError('config-changed', 'Concurrent edit'); },
  });
  await client.refresh(); fail = true;
  assert.equal((await client.refresh({ fresh: true })).model.id, 'old');
  assert.equal(client.lastError.code, 'config-changed');
});
