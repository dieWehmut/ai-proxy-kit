import test from 'node:test';
import assert from 'node:assert/strict';

import { KeyGroupClient, metadataUrl, modelsUrl } from '../group-sync.mjs';

const BASE = 'http://sub2api.test:8080/v1';

/** A fetch stand-in that records URLs and answers from a routing table. */
function fakeFetch(routes) {
  const calls = [];
  const impl = async (url) => {
    calls.push(url);
    const route = routes[url];
    if (!route) throw new Error('unexpected url ' + url);
    return {
      ok: route.status >= 200 && route.status < 300,
      status: route.status,
      json: async () => route.body,
    };
  };
  impl.calls = calls;
  return impl;
}

function client(fetchImpl) {
  const snapshots = [];
  const instance = new KeyGroupClient({
    baseUrl: BASE,
    key: 'test-key',
    statePath: undefined,
    configPath: undefined,
    fetchImpl,
    logger: { warn() {}, info() {} },
    onSnapshot: async (snapshot) => { snapshots.push(snapshot); },
  });
  instance.snapshots = snapshots;
  return instance;
}

const keyGroupBody = {
  object: 'sub2api.key_group',
  schema_version: 1,
  key: { id: 7, group_id: 12 },
  group: { id: 12, name: 'aistudio-gemini', platform: 'gemini', status: 'active' },
  models: [{ id: 'gemini-4-hydrogen', upstream_model: 'gemini-4-hydrogen', aliases: ['gemini-4-hydrogen'] }],
  revision: 'rev-1',
};

test('a 404 on the key-group endpoint falls back to the key-scoped model list', async () => {
  const fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { object: 'list', data: [{ id: 'gemini-4-hydrogen' }] } },
  });
  const sync = client(fetchImpl);

  const snapshot = await sync.refresh();

  assert.deepEqual(fetchImpl.calls, [metadataUrl(BASE), modelsUrl(BASE)]);
  assert.equal(snapshot.model.id, 'gemini-4-hydrogen');
  assert.equal(snapshot.group.name, 'key-models');
  assert.equal(sync.ambiguous, false);
  assert.equal(sync.snapshots.length, 1, 'the fallback snapshot is committed exactly once');
});

test('a 501 on the key-group endpoint also falls back', async () => {
  const fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 501, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { data: [{ id: 'gemini-4-hydrogen' }] } },
  });
  const sync = client(fetchImpl);
  const snapshot = await sync.refresh();
  assert.equal(snapshot.model.id, 'gemini-4-hydrogen');
});

test('an ambiguous key keeps the cache but suppresses every override', async () => {
  const fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: {
      status: 200,
      body: { data: [{ id: 'cn:deepseek-v4.1-flash' }, { id: 'gemini-4-hydrogen' }] },
    },
  });
  const sync = client(fetchImpl);

  const snapshot = await sync.refresh();

  assert.equal(snapshot, undefined, 'nothing is committed for an ambiguous key');
  assert.equal(sync.ambiguous, true);
  assert.equal(sync.snapshots.length, 0);
  assert.equal(sync.lastError.code, 'non-unique-model');
});

test('ambiguity clears again once the key is pinned to a single model', async () => {
  const ambiguous = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { data: [{ id: 'a' }, { id: 'b' }] } },
  });
  const sync = client(ambiguous);
  await sync.refresh();
  assert.equal(sync.ambiguous, true);

  sync.fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { data: [{ id: 'gemini-4-hydrogen' }] } },
  });
  const snapshot = await sync.refresh();

  assert.equal(sync.ambiguous, false);
  assert.equal(snapshot.model.id, 'gemini-4-hydrogen');
});

test('a patched backend is used directly and never consults the model list', async () => {
  const fetchImpl = fakeFetch({ [metadataUrl(BASE)]: { status: 200, body: keyGroupBody } });
  const sync = client(fetchImpl);

  const snapshot = await sync.refresh();

  assert.deepEqual(fetchImpl.calls, [metadataUrl(BASE)]);
  assert.equal(snapshot.group.platform, 'gemini');
  assert.equal(snapshot.model.id, 'gemini-4-hydrogen');
  assert.equal(sync.ambiguous, false);
});

test('a server error on the key-group endpoint is not treated as a missing endpoint', async () => {
  const fetchImpl = fakeFetch({ [metadataUrl(BASE)]: { status: 500, body: {} } });
  const sync = client(fetchImpl);

  const snapshot = await sync.refresh();

  assert.deepEqual(fetchImpl.calls, [metadataUrl(BASE)], 'no fallback for a genuine upstream error');
  assert.equal(snapshot, undefined);
  assert.equal(sync.ambiguous, false);
  assert.equal(sync.lastError.code, 'http-500');
});

test('an empty model list keeps the last model and never engages the ambiguity guard', async () => {
  const fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { object: 'list', data: [] } },
  });
  const sync = client(fetchImpl);

  const snapshot = await sync.refresh();

  assert.equal(snapshot, undefined);
  assert.equal(sync.ambiguous, false, 'an empty group is transient, not ambiguous');
  assert.equal(sync.lastError.code, 'empty-model-list');
});

test('the model-list fallback reuses the last authoritative platform', async () => {
  const deepseekKeyGroup = {
    ...keyGroupBody,
    key: { id: 8, group_id: 9 },
    group: { id: 9, name: 'workbuddy', platform: 'deepseek', status: 'active' },
    models: [{ id: 'cn:deepseek-v4.1-flash', upstream_model: 'cn:deepseek-v4.1-flash', aliases: ['cn:deepseek-v4.1-flash'] }],
  };
  const sync = client(fakeFetch({ [metadataUrl(BASE)]: { status: 200, body: deepseekKeyGroup } }));
  const first = await sync.refresh();
  assert.equal(first.group.platform, 'deepseek');

  // A single 404 must not silently downgrade a deepseek deployment to openai.
  sync.fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { data: [{ id: 'cn:deepseek-v4.1-flash' }] } },
  });
  const second = await sync.refresh();

  assert.equal(second.group.platform, 'deepseek');
  assert.equal(second.model.id, 'cn:deepseek-v4.1-flash');
});

test('a permanently ambiguous key is warned about hourly, not on every refresh', async () => {
  let clock = 1_000_000;
  const warnings = [];
  const fetchImpl = fakeFetch({
    [metadataUrl(BASE)]: { status: 404, body: {} },
    [modelsUrl(BASE)]: { status: 200, body: { data: [{ id: 'a' }, { id: 'b' }] } },
  });
  const sync = new KeyGroupClient({
    baseUrl: BASE,
    key: 'test-key',
    statePath: undefined,
    configPath: undefined,
    fetchImpl,
    logger: { warn: (message) => warnings.push(message), info() {} },
    now: () => clock,
  });

  await sync.refresh();
  clock += 31_000;
  await sync.refresh();
  assert.equal(warnings.length, 1, 'the second refresh 31s later must stay quiet');

  clock += 3_600_000;
  await sync.refresh();
  assert.equal(warnings.length, 2, 'an hour later the ambiguity is reported again');
});
