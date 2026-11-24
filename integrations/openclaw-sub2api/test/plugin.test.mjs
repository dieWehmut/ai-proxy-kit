import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { isManagedTurn, registerIntegration } from '../index.mjs';

const metadata = (id = 'model-a', groupId = 8) => ({
  object: 'sub2api.key_group', schema_version: 1,
  key: { id: 8, group_id: groupId }, group: { id: groupId, name: 'bot-group', platform: 'deepseek', status: 'active' },
  models: [{ id, upstream_model: 'cn:' + id, aliases: [id] }], revision: id + groupId,
});

function setup(t) {
  const dir = mkdtempSync(join(tmpdir(), 'sub2api-plugin-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const cfg = {
    models: { providers: { sub2api: { api: 'openai-completions', apiKey: 'fixture-key', models: [] } } },
    agents: { defaults: { model: { primary: 'sub2api/model-a', fallbacks: [] }, thinkingDefault: 'max' } },
    plugins: { entries: { 'sub2api-auto': { enabled: true } } },
  };
  const configPath = join(dir, 'openclaw.json'); writeFileSync(configPath, JSON.stringify(cfg));
  const hooks = {}; const providers = []; const services = []; const warnings = [];
  const api = { config: cfg, pluginConfig: { configPath, statePath: join(dir, 'state.json') },
    logger: { warn: (message) => warnings.push(message) },
    on: (name, hook) => { hooks[name] = hook; },
    registerProvider: (provider) => providers.push(provider),
    registerService: (service) => services.push(service),
  };
  return { dir, cfg, configPath, hooks, providers, services, warnings, api };
}

test('an existing session uses its key group model on the next turn without restart', async (t) => {
  const fixture = setup(t); let current = metadata(); let requests = 0;
  const integration = registerIntegration(fixture.api, {
    env: { SUB2API_API_URL: 'http://sub2api/v1', SUB2API_API_KEY: 'fixture-key' },
    fetchImpl: async () => { requests++; return { ok: true, json: async () => current }; },
  });
  const context = { modelProviderId: 'sub2api', modelId: 'model-a', sessionKey: 'same-existing-session' };
  const first = await fixture.hooks.before_model_resolve({ prompt: 'first' }, context);
  assert.equal(first.modelOverride, 'model-a');
  current = metadata('model-b', 9);
  const second = await fixture.hooks.before_model_resolve({ prompt: 'second' }, context);
  assert.equal(second.modelOverride, 'model-b');
  assert.deepEqual(second.fallbacksOverride, []);
  assert.equal(requests, 2);
  assert.equal(fixture.providers[0].resolveDynamicModel({ modelId: 'model-b' }).id, 'model-b');
  assert.equal(integration.client.current.group.id, 9);
  const saved = JSON.parse(readFileSync(fixture.configPath, 'utf8'));
  assert.equal(saved.agents.defaults.model.primary, 'sub2api/model-b');
  assert.equal(saved.agents.defaults.thinkingDefault, 'max');
});

test('explicit non-Sub2API selections and disabled plugins are untouched', async (t) => {
  const fixture = setup(t); let requests = 0;
  const integration = registerIntegration(fixture.api, {
    env: { SUB2API_API_URL: 'http://sub2api/v1', SUB2API_API_KEY: 'fixture-key' },
    fetchImpl: async () => { requests++; return { ok: true, json: async () => metadata() }; },
  });
  assert.equal(await fixture.hooks.before_model_resolve({}, { modelProviderId: 'other' }), undefined);
  assert.equal(requests, 0);
  fixture.cfg.plugins.entries['sub2api-auto'].enabled = false;
  writeFileSync(fixture.configPath, JSON.stringify(fixture.cfg));
  assert.equal(await fixture.hooks.before_model_resolve({}, { modelProviderId: 'sub2api' }), undefined);
  assert.equal(requests, 0);
});

test('cached model authentication keeps its identity before the next turn selects the new group', async (t) => {
  const fixture = setup(t); let current = metadata();
  const integration = registerIntegration(fixture.api, {
    env: { SUB2API_API_URL: 'http://sub2api/v1', SUB2API_API_KEY: 'fixture-key' },
    fetchImpl: async () => ({ ok: true, json: async () => current }),
  });
  const context = { modelProviderId: 'sub2api', modelId: 'model-a', sessionKey: 'same-session' };
  await fixture.hooks.before_model_resolve({}, context);
  current = metadata('model-b', 9);
  await integration.client.refresh({ fresh: true });
  const provider = fixture.providers[0];
  const cached = provider.resolveDynamicModel({ modelId: 'model-a' });
  assert.equal(cached.id, 'model-a');
  assert.equal(provider.normalizeResolvedModel({ model: cached }).id, 'model-a');
  assert.equal(cached.provider, 'sub2api');
  const next = await fixture.hooks.before_model_resolve({}, context);
  assert.equal(next.modelOverride, 'model-b');
  assert.equal(provider.resolveDynamicModel({ modelId: next.modelOverride }).id, 'model-b');
  assert.equal(integration.client.current.model.id, 'model-b');
});

test('service refresh is persistent and its timer is stopped with the gateway', async (t) => {
  const fixture = setup(t); let tick; let cleared = false; let group = metadata();
  const integration = registerIntegration(fixture.api, {
    env: { SUB2API_API_URL: 'http://sub2api/v1', SUB2API_API_KEY: 'fixture-key' },
    fetchImpl: async () => ({ ok: true, json: async () => group }),
    setInterval: (fn, interval) => { tick = fn; assert.equal(interval, 15000); return { unref() {} }; },
    clearInterval: () => { cleared = true; },
  });
  await fixture.services[0].start();
  group = metadata('model-b', 9); tick();
  // Wait on the specific live refresh, including its cross-process commit lock.
  await integration.client.inFlight;
  assert.equal(JSON.parse(readFileSync(fixture.configPath)).agents.defaults.model.primary, 'sub2api/model-b');
  fixture.services[0].stop(); assert.equal(cleared, true);
});

test('failed discovery returns the last model without emitting a chat system message', async (t) => {
  const fixture = setup(t); let unavailable = false;
  registerIntegration(fixture.api, {
    env: { SUB2API_API_URL: 'http://sub2api/v1', SUB2API_API_KEY: 'fixture-key' },
    fetchImpl: async () => { if (unavailable) throw new Error('network'); return { ok: true, json: async () => metadata() }; },
  });
  await fixture.hooks.before_model_resolve({}, { modelProviderId: 'sub2api' }); unavailable = true;
  const result = await fixture.hooks.before_model_resolve({}, { modelProviderId: 'sub2api' });
  assert.equal(result.modelOverride, 'model-a');
  assert.deepEqual(Object.keys(result).sort(), ['fallbacksOverride', 'modelOverride', 'providerOverride']);
  assert.equal(fixture.warnings.length, 1);
});

test('a non-Sub2API agent default is honored when a legacy context lacks a provider', () => {
  const cfg = { agents: { defaults: { model: { primary: 'sub2api/a' } }, entries: { other: { model: 'other/b' } } } };
  assert.equal(isManagedTurn({ agentId: 'other' }, cfg), false);
  assert.equal(isManagedTurn({ modelProviderId: 'other' }, cfg), false);
  assert.equal(isManagedTurn({}, cfg), true);
});
