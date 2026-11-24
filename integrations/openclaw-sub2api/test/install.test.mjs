import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { install, readEnv, removeManualModelValues } from '../install.mjs';

const response = {
  object: 'sub2api.key_group', schema_version: 1, key: { id: 8, group_id: 8 },
  group: { id: 8, name: 'workbuddy', platform: 'deepseek', status: 'active' },
  models: [{ id: 'cn:deepseek-v4.1-flash', upstream_model: 'cn:deepseek-v4.1-flash',
    aliases: ['cn:deepseek-v4.1-flash', 'deepseek-v4.1-flash', 'deepseek-flash'] }], revision: 'fixture',
};

function profile(t) {
  const root = mkdtempSync(join(tmpdir(), 'sub2api-install-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const cfg = {
    models: { mode: 'replace', providers: { sub2api: {
      baseUrl: '${SUB2API_API_URL}', apiKey: { source: 'env', provider: 'default', id: 'SUB2API_API_KEY' },
      api: 'openai-completions', models: [{ id: '${SUB2API_MODEL}', name: 'deepseek-v4.1-flash' }],
    } } },
    agents: { defaults: { model: { primary: '${ACTIVE_MODEL_PROVIDER}/${ACTIVE_MODEL_ID}', fallbacks: [] },
      modelPolicy: { allow: ['${ACTIVE_MODEL_PROVIDER}/${ACTIVE_MODEL_ID}'] },
      thinkingDefault: '${ACTIVE_MODEL_REASONING_EFFORT}' } },
    plugins: { allow: ['qq'], entries: { qq: { enabled: true } } },
    channels: { qq: { wsUrl: 'ws://fixture', accessToken: 'fixture-qq-key' } },
  };
  const env = 'SUB2API_API_URL=http://sub2api/v1\nSUB2API_API_KEY=fixture-private-key\nSUB2API_MODEL=deepseek-v4.1-flash\n' +
    'ACTIVE_MODEL_PROVIDER=sub2api\nACTIVE_MODEL_ID=deepseek-v4.1-flash\nACTIVE_MODEL_REASONING_EFFORT=max\nWB_API=fixture-old-key\n';
  writeFileSync(join(root, 'openclaw.json'), JSON.stringify(cfg)); writeFileSync(join(root, '.env'), env);
  return { root, cfg, env };
}

test('installer removes model env dependencies and validates against the running CLI before commit', async (t) => {
  const fixture = profile(t); let validation;
  const receipt = await install({ profile: fixture.root, pluginPath: '/fixture/plugin', backupRoot: join(fixture.root, 'backups') }, {
    fetchImpl: async () => ({ ok: true, json: async () => response }),
    validate: (path, env) => { validation = { cfg: JSON.parse(readFileSync(path)), env }; return { status: 0 }; },
  });
  const cfg = JSON.parse(readFileSync(join(fixture.root, 'openclaw.json')));
  const env = readEnv(readFileSync(join(fixture.root, '.env'), 'utf8'));
  assert.equal(cfg.agents.defaults.model.primary, 'sub2api/deepseek-v4.1-flash');
  assert.equal(cfg.agents.defaults.thinkingDefault, '${ACTIVE_MODEL_REASONING_EFFORT}');
  assert.deepEqual(cfg.agents.defaults.modelPolicy.allow, ['sub2api/*']);
  assert.equal(/\$\{(?:SUB2API_MODEL|ACTIVE_MODEL_PROVIDER|ACTIVE_MODEL_ID)\}/.test(JSON.stringify(cfg)), false);
  assert.deepEqual(cfg.channels, fixture.cfg.channels);
  assert.equal(env.SUB2API_API_KEY, 'fixture-private-key');
  assert.equal(env.WB_API, 'fixture-old-key');
  assert.equal(env.ACTIVE_MODEL_REASONING_EFFORT, 'max');
  for (const name of ['SUB2API_MODEL', 'ACTIVE_MODEL_PROVIDER', 'ACTIVE_MODEL_ID']) {
    assert.equal(env[name], undefined); assert.equal(validation.env[name], undefined);
  }
  assert.equal(receipt.key_id, 8);
  assert.equal(JSON.stringify(receipt).includes('fixture-private-key'), false);
  assert.equal(statSync(join(receipt.backup, '.env')).mode & 0o777, 0o600);
  assert.deepEqual(cfg.plugins.allow, ['qq', 'sub2api-auto']);
  assert.deepEqual(cfg.plugins.load.paths, ['/fixture/plugin']);
});

test('validation failure leaves the profile untouched', async (t) => {
  const fixture = profile(t);
  await assert.rejects(install({ profile: fixture.root }, {
    fetchImpl: async () => ({ ok: true, json: async () => response }), validate: () => ({ status: 1 }),
  }), /rejected the staged/);
  assert.equal(readFileSync(join(fixture.root, '.env'), 'utf8'), fixture.env);
  assert.deepEqual(JSON.parse(readFileSync(join(fixture.root, 'openclaw.json'))), fixture.cfg);
});

test('manual env removal handles export assignments without dropping other credentials', () => {
  const result = removeManualModelValues('export SUB2API_MODEL="old"\n # keep\nOTHER_KEY=keep\nACTIVE_MODEL_ID=old\n');
  assert.equal(readEnv(result).OTHER_KEY, 'keep');
  assert.equal(readEnv(result).SUB2API_MODEL, undefined);
  assert.ok(result.includes('# keep'));
});
