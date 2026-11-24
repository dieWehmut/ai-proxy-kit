import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { KeyGroupClient, runtimeModel } from './group-sync.mjs';

export const PLUGIN_ID = 'sub2api-auto';

function readConfig(path, fallback) {
  try { return JSON.parse(readFileSync(path, 'utf8')); }
  catch { return fallback; }
}

export function isManagedTurn(context, config) {
  if (config.plugins?.entries?.[PLUGIN_ID]?.enabled === false) return false;
  // Both installed OpenClaw versions supply the selected provider here.
  if (context?.modelProviderId) return context.modelProviderId === 'sub2api';
  const agentModel = context?.agentId && config.agents?.entries?.[context.agentId]?.model;
  const configured = agentModel ?? config.agents?.defaults?.model;
  const primary = typeof configured === 'string' ? configured : configured?.primary;
  return primary?.startsWith('sub2api/') ?? false;
}

/** Dependencies are injectable; production registration uses only Node builtins. */
export function registerIntegration(api, dependencies = {}) {
  const settings = api.pluginConfig ?? {};
  const configPath = settings.configPath ?? process.env.OPENCLAW_CONFIG_PATH ?? join(homedir(), '.openclaw/openclaw.json');
  const statePath = settings.statePath ?? join(homedir(), '.openclaw/state/sub2api-auto.json');
  const initialConfig = readConfig(configPath, api.config);
  const keyEnv = settings.keyEnv ?? 'SUB2API_API_KEY';
  const env = dependencies.env ?? process.env;
  const baseUrl = settings.baseUrl ?? env.SUB2API_API_URL;
  if (!baseUrl || !env[keyEnv]) {
    api.logger.warn('Sub2API group sync is not configured; set its API URL and key');
    return undefined;
  }
  const initialPrimary = initialConfig.agents?.defaults?.model;
  const reference = typeof initialPrimary === 'string' ? initialPrimary : initialPrimary?.primary;
  const preferredAlias = reference?.startsWith('sub2api/') ? reference.slice('sub2api/'.length) : undefined;
  const enabled = () => readConfig(configPath, api.config).plugins?.entries?.[PLUGIN_ID]?.enabled !== false;
  const client = new KeyGroupClient({ baseUrl, key: env[keyEnv], statePath, configPath,
    logger: api.logger, timeoutMs: settings.timeoutMs ?? 4000,
    preferredAlias, fetchImpl: dependencies.fetchImpl ?? fetch,
  });

  api.on('before_model_resolve', async (_event, context) => {
    const config = readConfig(configPath, api.config);
    if (!isManagedTurn(context, config)) return;
    const snapshot = await client.refresh({ fresh: true });
    if (!snapshot) return;
    return { providerOverride: 'sub2api', modelOverride: snapshot.model.id, fallbacksOverride: [] };
  }, { priority: 1000 });

  const currentDefinition = (context = {}) => {
    if (!enabled() || !client.current) return null;
    const definition = runtimeModel(client.current, baseUrl);
    const configured = readConfig(configPath, api.config).models?.providers?.sub2api?.models?.find((model) => model.id === definition.id);
    const resolved = configured ? { ...definition, ...configured, provider: definition.provider,
      api: definition.api, baseUrl: definition.baseUrl } : definition;
    // Newer OpenClaw rematerializes the cached session model for authentication
    // before running the turn hook. That phase requires the requested identity;
    // before_model_resolve then selects the fresh key-group model for generation.
    const id = typeof context.modelId === 'string' && context.modelId ? context.modelId : definition.id;
    return { ...resolved, id };
  };

  api.registerProvider({
    id: 'sub2api', label: 'Sub2API (current key group)', envVars: [keyEnv], auth: [],
    resolveDynamicModel: currentDefinition,
    // Node22 OpenClaw expects void here, while newer versions also accept a model.
    prepareDynamicModel: async () => { if (enabled()) await client.refresh({ fresh: true }); },
    preferRuntimeResolvedModel: () => enabled(),
    normalizeResolvedModel: ({ model }) => currentDefinition({ modelId: model.id }) ?? model,
  });

  const setTimer = dependencies.setInterval ?? setInterval;
  const clearTimer = dependencies.clearInterval ?? clearInterval;
  let timer;
  api.registerService({
    id: 'sub2api-auto-sync',
    start: async () => {
      await client.refresh();
      timer = setTimer(() => { void client.refresh(); }, settings.refreshIntervalMs ?? 15000);
      timer.unref?.();
    },
    stop: () => { if (timer !== undefined) clearTimer(timer); timer = undefined; },
  });
  return { client };
}

export default {
  id: PLUGIN_ID,
  name: 'Sub2API Group Model Sync',
  version: '0.1.0',
  register: registerIntegration,
};
