#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { KeyGroupClient, keyFingerprint, privateWrite, updateConfig } from './group-sync.mjs';
import { PLUGIN_ID } from './index.mjs';

export const MANUAL_MODEL_KEYS = new Set(['SUB2API_MODEL', 'ACTIVE_MODEL_PROVIDER', 'ACTIVE_MODEL_ID']);

export function readEnv(text) {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2];
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    values[match[1]] = value;
  }
  return values;
}

export function removeManualModelValues(text) {
  return text.split(/\r?\n/).filter((line) => {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=/);
    return !match || !MANUAL_MODEL_KEYS.has(match[1]);
  }).join('\n').replace(/\n*$/, '\n');
}

function expandEnv(text, values) {
  return String(text ?? '').replace(/\$\{([A-Za-z_][A-Za-z_0-9]*)\}/g, (_all, name) => values[name] ?? '');
}

function migrateManualModelRefs(value, values) {
  const expand = (text) => text.replace(/\$\{([A-Za-z_][A-Za-z_0-9]*)\}/g,
    (all, name) => MANUAL_MODEL_KEYS.has(name) ? (values[name] ?? '') : all);
  if (typeof value === 'string') return expand(value);
  if (Array.isArray(value)) return value.map((entry) => migrateManualModelRefs(entry, values));
  if (value && typeof value === 'object') {
    const entries = Object.entries(value).map(([name, entry]) => [expand(name), migrateManualModelRefs(entry, values)]);
    if (new Set(entries.map(([name]) => name)).size !== entries.length) throw new Error('Model environment migration would merge conflicting config keys');
    return Object.fromEntries(entries);
  }
  return value;
}

export async function install(options = {}, dependencies = {}) {
  const root = options.profile ?? join(homedir(), '.openclaw');
  const configPath = options.configPath ?? join(root, 'openclaw.json');
  const envPath = options.envPath ?? join(root, '.env');
  const statePath = options.statePath ?? join(root, 'state/sub2api-auto.json');
  const pluginPath = options.pluginPath ?? dirname(fileURLToPath(import.meta.url));
  const oldConfig = readFileSync(configPath, 'utf8');
  const oldEnv = readFileSync(envPath, 'utf8');
  const oldState = existsSync(statePath) ? readFileSync(statePath, 'utf8') : undefined;
  const values = readEnv(oldEnv);
  const baseUrl = values.SUB2API_API_URL;
  const key = values.SUB2API_API_KEY;
  if (!baseUrl || !key) throw new Error('The profile must contain SUB2API_API_URL and SUB2API_API_KEY');
  const config = migrateManualModelRefs(JSON.parse(oldConfig), values);
  const primary = config.agents?.defaults?.model;
  const expanded = expandEnv(typeof primary === 'string' ? primary : primary?.primary, values);
  if (!expanded.startsWith('sub2api/')) throw new Error('Installation requires an active Sub2API profile');
  config.agents.defaults.model = { ...((typeof primary === 'object' && primary) || {}), primary: expanded, fallbacks: [] };
  for (const model of config.models?.providers?.sub2api?.models ?? []) model.id = expandEnv(model.id, values);
  const client = new KeyGroupClient({ baseUrl, key, preferredAlias: expanded.slice('sub2api/'.length),
    fetchImpl: dependencies.fetchImpl ?? fetch, logger: { warn() {} } });
  const snapshot = await client.refresh({ fresh: true });
  if (!snapshot) throw new Error('Sub2API key group discovery failed: ' + client.lastError?.code);
  const next = updateConfig(config, snapshot, baseUrl);
  next.plugins ??= {};
  if (next.plugins.allow && !next.plugins.allow.includes(PLUGIN_ID)) next.plugins.allow.push(PLUGIN_ID);
  next.plugins.load ??= {};
  next.plugins.load.paths ??= [];
  if (!next.plugins.load.paths.includes(pluginPath)) next.plugins.load.paths.push(pluginPath);
  next.plugins.entries ??= {};
  next.plugins.entries[PLUGIN_ID] = { enabled: true, config: {
    baseUrl, keyEnv: 'SUB2API_API_KEY', configPath, statePath, refreshIntervalMs: 15000, timeoutMs: 4000,
  } };
  const nextConfig = JSON.stringify(next, null, 2) + '\n';
  if (/\$\{(?:SUB2API_MODEL|ACTIVE_MODEL_PROVIDER|ACTIVE_MODEL_ID)\}/.test(nextConfig)) {
    throw new Error('Removed model environment references remain in OpenClaw configuration');
  }
  const nextEnv = removeManualModelValues(oldEnv);
  const nextState = JSON.stringify({ ...snapshot, fingerprint: keyFingerprint(key), base_url: baseUrl.replace(/\/+$/, '') }, null, 2) + '\n';
  const node = options.nodePath ?? process.execPath;
  const entry = options.openclawEntry ?? resolve(dirname(node), '../lib/node_modules/openclaw/dist/index.js');
  const stage = mkdtempSync(join(root, '.sub2api-auto-stage-'));
  try {
    const stageConfig = join(stage, 'openclaw.json');
    privateWrite(stageConfig, nextConfig); privateWrite(join(stage, '.env'), nextEnv);
    const validationEnv = { ...process.env, ...readEnv(nextEnv), OPENCLAW_CONFIG_PATH: stageConfig };
    validationEnv.PATH = dirname(node) + ':' + (validationEnv.PATH ?? '');
    for (const name of MANUAL_MODEL_KEYS) delete validationEnv[name];
    const validate = dependencies.validate ?? ((path, env) => spawnSync(node, [entry, 'config', 'validate', '--json'], {
      env, encoding: 'utf8', timeout: 45000, maxBuffer: 2 * 1024 * 1024,
    }));
    const result = validate(stageConfig, validationEnv);
    if (result.status !== 0) {
      // Avoid printing CLI diagnostic values: they can contain profile secrets.
      throw new Error('The installed OpenClaw rejected the staged group-managed configuration');
    }
  } finally { rmSync(stage, { recursive: true, force: true }); }

  const backupRoot = options.backupRoot ?? join(homedir(), 'openclaw-config-backups');
  mkdirSync(backupRoot, { recursive: true, mode: 0o700 });
  const prefix = 'sub2api-auto-' + new Date().toISOString().replace(/[:.]/g, '-') + '-';
  const backup = mkdtempSync(join(backupRoot, prefix));
  privateWrite(join(backup, 'openclaw.json'), oldConfig);
  privateWrite(join(backup, '.env'), oldEnv);
  if (oldState !== undefined) privateWrite(join(backup, 'state.json'), oldState);
  const applied = [];
  try {
    privateWrite(envPath, nextEnv, oldEnv); applied.push([envPath, oldEnv, nextEnv]);
    privateWrite(configPath, nextConfig, oldConfig); applied.push([configPath, oldConfig, nextConfig]);
    privateWrite(statePath, nextState, oldState); applied.push([statePath, oldState, nextState]);
  } catch (error) {
    for (const [path, before, after] of applied.reverse()) {
      try { if (before !== undefined) privateWrite(path, before, after); else rmSync(path, { force: true }); }
      catch { /* The private backup remains available if an external edit blocks rollback. */ }
    }
    throw new Error('Configuration installation failed; preserved backup: ' + backup);
  }
  const receipt = { plugin: PLUGIN_ID, key_id: snapshot.key_id, group: snapshot.group,
    model: snapshot.model.id, upstream_model: snapshot.model.upstream_model,
    backup, config_validated: true, removed_model_environment: [...MANUAL_MODEL_KEYS], restart_required: true };
  privateWrite(join(backup, 'installation.json'), JSON.stringify(receipt, null, 2) + '\n');
  return receipt;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const names = { '--profile': 'profile', '--config': 'configPath', '--env': 'envPath', '--state': 'statePath',
    '--plugin': 'pluginPath', '--node': 'nodePath', '--openclaw-entry': 'openclawEntry', '--backup-root': 'backupRoot' };
  const options = {};
  try {
    for (let index = 2; index < process.argv.length; index += 2) {
      const name = names[process.argv[index]];
      if (!name || !process.argv[index + 1]) throw new Error('Unknown or missing installer option');
      options[name] = process.argv[index + 1];
    }
    console.log(JSON.stringify(await install(options)));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
