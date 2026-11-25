import test from 'node:test';
import assert from 'node:assert/strict';

import * as mod from '../group-sync.mjs';

// Guard every helper lookup so a missing export fails with a clear message.
function exported(name) {
  const value = mod[name];
  assert.equal(typeof value, 'function', `group-sync.mjs must export ${name}() as a function`);
  return value;
}

function assertNonUniqueModel(fn) {
  assert.throws(fn, (error) => {
    assert.ok(error instanceof mod.DiscoveryError, `expected DiscoveryError, got ${error?.constructor?.name}`);
    assert.ok(!(error instanceof TypeError), 'must not be a TypeError');
    assert.equal(error.name, 'DiscoveryError');
    assert.equal(error.code, 'non-unique-model');
    return true;
  });
}

test('module exports the models discovery helpers', () => {
  assert.equal(typeof mod.DiscoveryError, 'function');
  exported('modelsUrl');
  exported('normalizeModelsList');
});

test('modelsUrl trims trailing slashes and appends /models', () => {
  const modelsUrl = exported('modelsUrl');
  assert.equal(modelsUrl('http://h:8080/v1'), 'http://h:8080/v1/models');
  assert.equal(modelsUrl('http://h:8080/v1/'), 'http://h:8080/v1/models');
  assert.equal(modelsUrl('http://h:8080/v1///'), 'http://h:8080/v1/models');
});

test('single model normalizes to a complete snapshot', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  const snapshot = normalizeModelsList({ object: 'list', data: [{ id: 'gpt-4o', object: 'model' }] });
  assert.deepEqual(snapshot, {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
});

test('preferredAlias wins when present among ids', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  const snapshot = normalizeModelsList({ object: 'list', data: [{ id: 'gpt-4o' }] }, 'gpt-4o');
  assert.deepEqual(snapshot, {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
});

test('preferredAlias is ignored when absent from ids', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  const snapshot = normalizeModelsList({ object: 'list', data: [{ id: 'gpt-4o' }] }, 'not-a-model');
  assert.deepEqual(snapshot, {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
  // Undefined preferredAlias behaves the same way.
  assert.deepEqual(normalizeModelsList({ data: [{ id: 'gpt-4o' }] }, undefined), {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
});

test('duplicate ids collapse to one without false ambiguity', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  const snapshot = normalizeModelsList({
    object: 'list',
    data: [{ id: 'gpt-4o' }, { id: 'gpt-4o' }, { id: 'gpt-4o' }],
  });
  assert.deepEqual(snapshot, {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
});

test('zero models is rejected as non-unique', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  assertNonUniqueModel(() => normalizeModelsList({ object: 'list', data: [] }));
});

test('two distinct models are rejected as non-unique', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  assertNonUniqueModel(() => normalizeModelsList({ object: 'list', data: [{ id: 'a' }, { id: 'b' }] }));
});

test('malformed entries are skipped', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  const snapshot = normalizeModelsList({
    object: 'list',
    data: [{ id: 'gpt-4o' }, {}, { id: '' }, { id: '   ' }, { id: 42 }, { id: null }, null, 'string'],
  });
  assert.deepEqual(snapshot, {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
});

test('all-malformed entries leave no ids and throw', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  assertNonUniqueModel(() => normalizeModelsList({
    object: 'list',
    data: [{}, { id: '' }, { id: '  ' }, { id: 7 }, null],
  }));
});

test('bare array payload is accepted', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  assert.deepEqual(normalizeModelsList([{ id: 'gpt-4o' }]), {
    model: { id: 'gpt-4o', upstream_model: 'gpt-4o', aliases: ['gpt-4o'] },
  });
  assertNonUniqueModel(() => normalizeModelsList([{ id: 'a' }, { id: 'b' }]));
});

test('non-object payloads throw DiscoveryError rather than TypeError', () => {
  const normalizeModelsList = exported('normalizeModelsList');
  for (const payload of [null, 'x', 42]) {
    assertNonUniqueModel(() => normalizeModelsList(payload));
  }
});

test('DiscoveryError carries name and a string code', () => {
  const error = new mod.DiscoveryError('non-unique-model', 'boom');
  assert.equal(error.name, 'DiscoveryError');
  assert.equal(typeof error.code, 'string');
  assert.equal(error.code, 'non-unique-model');
  assert.ok(error instanceof Error);
  assert.ok(!(error instanceof TypeError));
});
