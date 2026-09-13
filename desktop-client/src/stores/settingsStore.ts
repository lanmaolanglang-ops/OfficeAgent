import { create } from 'zustand';
import type { AppSettings, AgentType, ModelConfig } from '../types';

const STORAGE_KEY = 'officeagent_settings';

// P5-4（审计 M36③）：这里曾硬编码一份前端模型清单（gpt-5.6-sol /
// claude-opus-5 / qwen3.8-max-preview …），既与后端权威目录
// （office_agent/models/model_schemas.py 的 default_model_catalog()）双源，
// 又含后端不认识的虚构版本号，provider 名也不对（后端 canonical 是 `claude`，
// `anthropic` 只是别名）。前端不再维护模型清单：已配置模型以后端
// `GET /api/settings/model` 为准，本 store 只保存 UI 偏好。
const LEGACY_MODEL_UPGRADE: Record<string, string> = {
  'gpt-5.4': 'gpt-5.6-sol',
  'doubao-seed-2.1-pro': 'doubao-seed-2-1-pro',
  'qwen3.8-max': 'qwen3.8-max-preview',
};

const defaultSettings: AppSettings = {
  backend_url: 'http://127.0.0.1:8765',
  default_agent: 'auto',
  models: [],
  auto_open_results: true,
  notifications: true,
  autostart: false,
  minimize_to_tray: true,
};

const AGENT_TYPES: readonly string[] = ['auto', 'word', 'ppt', 'excel'];

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback;
}

function asBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function stripApiKeys(models: unknown): ModelConfig[] {
  // API Key 只保存在后端 models.json，localStorage 一律清空。
  // 非对象条目（数组/标量/被污染的持久化数据）直接丢弃。
  if (!Array.isArray(models)) return [];
  return models
    .filter(isPlainObject)
    .map((m) => ({ ...(m as unknown as ModelConfig), api_key: '' }));
}

/**
 * 读取持久化设置。持久化数据是不可信输入：数组 / null / 字段类型错位都必须
 * 安全降级（修复前直接展开数组会产出 "0"/"1" 之类的伪键）。
 */
export function parsePersistedSettings(raw: string | null): AppSettings {
  if (!raw) return defaultSettings;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return defaultSettings;
  }
  if (!isPlainObject(parsed)) return defaultSettings;

  const defaultAgent = typeof parsed.default_agent === 'string'
    && AGENT_TYPES.includes(parsed.default_agent)
    ? parsed.default_agent as AgentType
    : defaultSettings.default_agent;

  const models = stripApiKeys(parsed.models).map((m: ModelConfig) => {
    const upgraded = LEGACY_MODEL_UPGRADE[m.model];
    return upgraded ? { ...m, model: upgraded } : m;
  });

  return {
    backend_url: asString(parsed.backend_url, defaultSettings.backend_url),
    default_agent: defaultAgent,
    models,
    auto_open_results: asBoolean(parsed.auto_open_results, defaultSettings.auto_open_results),
    notifications: asBoolean(parsed.notifications, defaultSettings.notifications),
    autostart: asBoolean(parsed.autostart, defaultSettings.autostart),
    minimize_to_tray: asBoolean(parsed.minimize_to_tray, defaultSettings.minimize_to_tray),
  };
}

function loadSettings(): AppSettings {
  try {
    return parsePersistedSettings(localStorage.getItem(STORAGE_KEY));
  } catch {
    return defaultSettings;
  }
}

function saveSettings(settings: AppSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...settings, models: stripApiKeys(settings.models) }));
    localStorage.setItem('backend_url', settings.backend_url);
  } catch {
    // ignore
  }
}

interface SettingsState {
  settings: AppSettings;
  updateSettings: (updates: Partial<AppSettings>) => void;
  setBackendUrl: (url: string) => void;
  setDefaultAgent: (agent: AgentType) => void;
  resetSettings: () => void;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: loadSettings(),

  updateSettings: (updates) => {
    const newSettings = { ...get().settings, ...updates };
    saveSettings(newSettings);
    set({ settings: newSettings });
  },

  setBackendUrl: (url) => {
    get().updateSettings({ backend_url: url });
  },

  setDefaultAgent: (agent) => {
    get().updateSettings({ default_agent: agent });
  },

  resetSettings: () => {
    saveSettings(defaultSettings);
    set({ settings: defaultSettings });
  },
}));
