import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DiscoveryError, KeyGroupClient, normalizeMetadata, privateWrite, runtimeModel,
  updateConfig, withFileLock, writeOpenClawConfig } from '../group-sync.mjs';

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

test('same-target refresh preserves user model options and provider-wide policy', () => {
  const old = config(); const snapshot = normalizeMetadata(fixture('old-model', 'cn:old-model'));
  const options = { alias: 'My DeepSeek', params: { temperature: 0.3, maxTokens: 64000 }, agentRuntime: 'pi' };
  old.agents.defaults.models['sub2api/old-model'] = options;
  old.agents.defaults.models['sub2api/*'] = { params: { cacheRetention: 'long' } };
  old.models.providers.sub2api.models[0].maxTokens = 128000;
  old.models.providers.sub2api.models[0].contextWindow = 1000000;
  const next = updateConfig(old, snapshot, 'http://sub2api/v1', snapshot);
  assert.deepEqual(next.agents.defaults.models['sub2api/old-model'], options);
  assert.deepEqual(next.agents.defaults.models['sub2api/*'], { params: { cacheRetention: 'long' } });
  assert.equal(next.models.providers.sub2api.models[0].maxTokens, 128000);
  assert.equal(next.models.providers.sub2api.models[0].contextWindow, 1000000);
  const different = normalizeMetadata(fixture('new-model', 'cn:new-model'));
  const changed = updateConfig(old, different, 'http://sub2api/v1', snapshot);
  assert.deepEqual(changed.agents.defaults.models['sub2api/new-model'], { alias: 'new-model' });
  assert.equal(changed.agents.defaults.models['sub2api/old-model'], undefined);
});

test('independent clients cannot let an old background response roll back new config or cache', async (t) => {
  const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
  let releaseOld; let startedOld;
  const started = new Promise((resolve) => { startedOld = resolve; });
  const delayed = new Promise((resolve) => { releaseOld = resolve; });
  const options = { baseUrl: 'http://sub2api/v1', key: 'fixture-key', statePath,
    onSnapshot: (snapshot, previous) => writeOpenClawConfig(configPath, snapshot, 'http://sub2api/v1', previous) };
  const backgroundClient = new KeyGroupClient({ ...options, fetchImpl: async () => {
    startedOld(); return await delayed;
  } });
  const oldRequest = backgroundClient.refresh(); await started;
  const turnClient = new KeyGroupClient({ ...options, fetchImpl: async () => ({
    ok: true, json: async () => fixture('new-model', 'cn:new-model', 9),
  }) });
  assert.equal((await turnClient.refresh({ fresh: true })).model.id, 'new-model');
  releaseOld({ ok: true, json: async () => fixture('old-model', 'cn:old-model', 8) });
  assert.equal((await oldRequest).model.id, 'new-model');
  assert.equal(JSON.parse(readFileSync(configPath)).agents.defaults.model.primary, 'sub2api/new-model');
  const stored = JSON.parse(readFileSync(statePath));
  assert.equal(stored.model.id, 'new-model'); assert.equal(stored.generation, 2);
  assert.equal(new KeyGroupClient(options).current.model.id, 'new-model');
});

test('separate Node processes share ordered commits, including a late old response', { timeout: 10000 }, async (t) => {
  const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
  const moduleUrl = new URL('../group-sync.mjs', import.meta.url).href;
  const program = [
    'import { KeyGroupClient, writeOpenClawConfig } from ' + JSON.stringify(moduleUrl) + ';',
    'const response = JSON.parse(process.env.GROUP_SYNC_RESPONSE);',
    'const client = new KeyGroupClient({baseUrl:"http://sub2api/v1",key:"fixture-key",statePath:process.env.GROUP_SYNC_STATE_PATH,',
    'onSnapshot:(snapshot,previous)=>writeOpenClawConfig(process.env.GROUP_SYNC_CONFIG_PATH,snapshot,"http://sub2api/v1",previous),',
    'fetchImpl:async()=>{if(process.env.GROUP_SYNC_HOLD==="1"){process.stdout.write("READY\\n");process.stdin.resume();await new Promise(resolve=>process.stdin.once("end",resolve));}return {ok:true,json:async()=>response};}});',
    'const result=await client.refresh({fresh:process.env.GROUP_SYNC_HOLD!=="1"});',
    'console.log(JSON.stringify({model:result.model.id,generation:result.generation}));',
  ].join('\n');
  const children = [];
  t.after(() => { for (const child of children) if (child.exitCode === null) child.kill('SIGTERM'); });
  const start = (response, hold) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', program], { env: {
      ...process.env, GROUP_SYNC_STATE_PATH: statePath, GROUP_SYNC_CONFIG_PATH: configPath,
      GROUP_SYNC_RESPONSE: JSON.stringify(response), GROUP_SYNC_HOLD: hold ? '1' : '0',
    }, stdio: ['pipe', 'pipe', 'pipe'] });
    children.push(child); let output = ''; let error = ''; let readyResolve; let readyReject;
    const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
    child.stdout.on('data', (chunk) => { output += chunk.toString(); if (output.includes('READY\n')) readyResolve(); });
    child.stderr.on('data', (chunk) => { error += chunk.toString(); });
    const done = new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', (code) => {
        if (code !== 0) { const reason = new Error('Child failed: ' + error); readyReject(reason); reject(reason); }
        else resolve(JSON.parse(output.trim().split('\n').at(-1)));
      });
    });
    return { child, ready, done };
  };
  const old = start(fixture('old-model', 'cn:old-model', 8), true); await old.ready;
  const current = start(fixture('new-model', 'cn:new-model', 9), false);
  assert.equal((await current.done).model, 'new-model');
  old.child.stdin.end();
  assert.equal((await old.done).model, 'new-model');
  assert.equal(JSON.parse(readFileSync(statePath)).model.id, 'new-model');
  assert.equal(JSON.parse(readFileSync(configPath)).agents.defaults.model.primary, 'sub2api/new-model');
});

for (const change of ['key', 'base-url']) {
  test('an older ' + change + ' writer cannot overwrite another profile identity or adopt its fallback', async (t) => {
    const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
    let delayed = false; let releaseOld; let oldStarted; let oldCalls = 0;
    const started = new Promise((resolve) => { oldStarted = resolve; });
    const held = new Promise((resolve) => { releaseOld = resolve; });
    const old = new KeyGroupClient({ statePath, key: 'old-key', baseUrl: 'http://old/v1',
      onSnapshot: (snapshot, previous, guard) => writeOpenClawConfig(configPath, snapshot, 'http://old/v1', previous, guard),
      fetchImpl: async () => {
        oldCalls++;
        if (delayed) { oldStarted(); return await held; }
        return { ok: true, json: async () => fixture('old-model', 'cn:old-model', 8) };
      }, logger: { warn() {} },
    });
    await old.refresh(); delayed = true;
    const oldRequest = old.refresh({ fresh: true }); await started;
    const currentBase = change === 'base-url' ? 'http://new/v1' : 'http://old/v1';
    const current = new KeyGroupClient({ statePath, key: change === 'key' ? 'new-key' : 'old-key', baseUrl: currentBase,
      onSnapshot: (snapshot, previous, guard) => writeOpenClawConfig(configPath, snapshot, currentBase, previous, guard),
      fetchImpl: async () => {
        const data = fixture('new-model', 'cn:new-model', 9); data.key.id = 9;
        return { ok: true, json: async () => data };
      },
    });
    await current.refresh({ fresh: true });
    releaseOld({ ok: true, json: async () => fixture('old-model', 'cn:old-model', 8) });
    const result = await oldRequest;
    assert.equal(result.key_id, 8);
    assert.equal(old.superseded, true);
    const stored = JSON.parse(readFileSync(statePath));
    assert.equal(stored.key_id, 9); assert.equal(stored.generation, 3);
    assert.equal(stored.base_url, currentBase);
    assert.equal(JSON.parse(readFileSync(configPath)).agents.defaults.model.primary, 'sub2api/new-model');
    await old.refresh({ fresh: true }); assert.equal(oldCalls, 2);
    assert.equal(new KeyGroupClient({ statePath, key: change === 'key' ? 'new-key' : 'old-key', baseUrl: currentBase }).current.model.id, 'new-model');
  });
}

test('cache write failure cannot publish a new configuration or roll it back after offline restart', async (t) => {
  const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
  let latest = fixture('old-model', 'cn:old-model');
  const options = { statePath, configPath, key: 'fixture-key', baseUrl: 'http://sub2api/v1', logger: { warn() {} },
    fetchImpl: async () => ({ ok: true, json: async () => latest }) };
  const client = new KeyGroupClient(options); await client.refresh();
  const beforeConfig = readFileSync(configPath, 'utf8'); const beforeState = readFileSync(statePath, 'utf8');
  latest = fixture('new-model', 'cn:new-model', 9);
  client.writeState = () => { throw new Error('Injected storage I/O failure'); };
  assert.equal((await client.refresh({ fresh: true })).model.id, 'old-model');
  assert.equal(client.lastError.code, 'persistence-unavailable');
  assert.equal(readFileSync(configPath, 'utf8'), beforeConfig);
  assert.equal(readFileSync(statePath, 'utf8'), beforeState);
  const restarted = new KeyGroupClient({ ...options, fetchImpl: async () => { throw new Error('Offline'); } });
  assert.equal((await restarted.refresh({ fresh: true })).model.id, 'old-model');
  assert.equal(readFileSync(configPath, 'utf8'), beforeConfig);
});

test('configuration write failure restores the cache in the same recovery transaction', async (t) => {
  const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
  let latest = fixture('old-model', 'cn:old-model');
  const client = new KeyGroupClient({ statePath, configPath, key: 'fixture-key', baseUrl: 'http://sub2api/v1', logger: { warn() {} },
    fetchImpl: async () => ({ ok: true, json: async () => latest }) });
  await client.refresh(); const beforeState = readFileSync(statePath, 'utf8'); const beforeConfig = readFileSync(configPath, 'utf8');
  latest = fixture('new-model', 'cn:new-model', 9);
  client.writeConfig = () => { throw new Error('Injected config I/O failure'); };
  assert.equal((await client.refresh({ fresh: true })).model.id, 'old-model');
  assert.equal(readFileSync(statePath, 'utf8'), beforeState);
  assert.equal(readFileSync(configPath, 'utf8'), beforeConfig);
  assert.equal(client.lastError.code, 'persistence-unavailable');
});

for (const complete of [false, true]) {
  test('offline restart recovers a ' + (complete ? 'completed' : 'partially applied') + ' configuration/cache transaction', async (t) => {
    const { statePath, configPath } = setup(t); writeFileSync(configPath, JSON.stringify(config()));
    const options = { statePath, configPath, key: 'fixture-key', baseUrl: 'http://sub2api/v1', logger: { warn() {} },
      fetchImpl: async () => ({ ok: true, json: async () => fixture('old-model', 'cn:old-model') }) };
    const client = new KeyGroupClient(options); await client.refresh();
    const beforeState = readFileSync(statePath, 'utf8'); const beforeConfig = readFileSync(configPath, 'utf8');
    const next = { ...normalizeMetadata(fixture('new-model', 'cn:new-model', 9)), generation: 2, identity_generation: 1 };
    const afterState = client.stateText(next);
    const afterConfig = JSON.stringify(updateConfig(JSON.parse(beforeConfig), next, client.baseUrl, client.current), null, 2) + '\n';
    privateWrite(client.transactionPath(), JSON.stringify({ generation: 2, state_path: statePath, config_path: configPath,
      state_before: beforeState, state_after: afterState, config_before: beforeConfig, config_after: afterConfig }));
    privateWrite(statePath, afterState);
    privateWrite(statePath + '.sequence.json', JSON.stringify({ generation: 2 }));
    if (complete) privateWrite(configPath, afterConfig);
    const restarted = new KeyGroupClient({ ...options, fetchImpl: async () => { throw new Error('Offline'); } });
    const expected = complete ? 'new-model' : 'old-model';
    assert.equal(restarted.current.model.id, expected);
    assert.equal((await restarted.refresh({ fresh: true })).model.id, expected);
    assert.equal(readFileSync(configPath, 'utf8'), complete ? afterConfig : beforeConfig);
    assert.equal(readFileSync(statePath, 'utf8'), complete ? afterState : beforeState);
  });
}

test('losing an acquired lock worker prevents an old operation from committing', { timeout: 10000 }, async (t) => {
  const { dir } = setup(t); const lockPath = join(dir, 'shared.lock'); const target = join(dir, 'value.json');
  privateWrite(target, 'old'); let started; let release; let workerPid;
  const ready = new Promise((resolve) => { started = resolve; }); const held = new Promise((resolve) => { release = resolve; });
  const stale = withFileLock(lockPath, async (guard) => {
    workerPid = guard.workerPid; started(); await held;
    privateWrite(target, 'stale', undefined, guard);
    return 'unexpected-success';
  });
  await ready; process.kill(workerPid, 'SIGKILL');
  await withFileLock(lockPath, (guard) => { privateWrite(target, 'new', undefined, guard); });
  release();
  await assert.rejects(stale, (error) => error.code === 'coordination-lost');
  assert.equal(readFileSync(target, 'utf8'), 'new');
});
