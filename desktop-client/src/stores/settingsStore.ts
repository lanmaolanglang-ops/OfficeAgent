import { create } from 'zustand';
import type { AppSettings, AgentType, ModelConfig } from '../types';

const STORAGE_KEY = 'officeagent_settings';

const defaultModels: ModelConfig[] = [
  { provider: 'openai', model: 'gpt-5.6-sol', api_key: '', enabled: false },
  { provider: 'anthropic', model: 'claude-opus-5', api_key: '', enabled: false },
  { provider: 'deepseek', model: 'deepseek-chat', api_key: '', enabled: false },
  { provider: 'doubao', model: 'doubao-seed-2-1-pro', api_key: '', enabled: true },
  { provider: 'qwen', model: 'qwen3.8-max-preview', api_key: '', enabled: false },
  { provider: 'agnes', model: 'agnes-2.0-flash', api_key: '', enabled: false, base_url: 'https://apihub.agnes-ai.cn/v1' },
];

const defaultSettings: AppSettings = {
  backend_url: 'http://127.0.0.1:8765',
  default_agent: 'auto',
  models: defaultModels,
  auto_open_results: true,
  notifications: true,
  autostart: false,
  minimize_to_tray: true,
};

function stripApiKeys(models: ModelConfig[]): ModelConfig[] {
  // API Key 只保存在后端 models.json，localStorage 一律清空
  return (models || []).map((m) => ({ ...m, api_key: '' }));
}

function loadSettings(): AppSettings {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      // 清除历史版本可能残留的 API Key
      parsed.models = stripApiKeys(parsed.models || []);
      // 旧版本模型名称自动升级到最新推荐模型
      const LEGACY_MODEL_UPGRADE: Record<string, string> = {
        'gpt-5.4': 'gpt-5.6-sol',
        'doubao-seed-2.1-pro': 'doubao-seed-2-1-pro',
        'qwen3.8-max': 'qwen3.8-max-preview',
      };
      parsed.models = (parsed.models || []).map((m: ModelConfig) => {
        const upgraded = LEGACY_MODEL_UPGRADE[m.model];
        return upgraded ? { ...m, model: upgraded } : m;
      });
      // 合并默认模型，确保新增的provider也存在
      const existingProviders = new Set((parsed.models || []).map((m: ModelConfig) => m.provider));
      const mergedModels = [
        ...(parsed.models || []),
        ...defaultModels.filter((m) => !existingProviders.has(m.provider)),
      ];
      return { ...defaultSettings, ...parsed, models: mergedModels };
    }
  } catch {
    // ignore
  }
  return defaultSettings;
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
  updateModel: (provider: string, updates: Partial<ModelConfig>) => void;
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

  updateModel: (provider, updates) => {
    const existing = get().settings.models.find((m) => m.provider === provider);
    let models;
    if (existing) {
      models = get().settings.models.map((m) =>
        m.provider === provider ? { ...m, ...updates } : m
      );
    } else {
      // 添加新的provider配置
      const defaultModel = defaultModels.find((m) => m.provider === provider);
      models = [...get().settings.models, { ...defaultModel, ...updates, provider } as ModelConfig];
    }
    get().updateSettings({ models });
  },

  resetSettings: () => {
    saveSettings(defaultSettings);
    set({ settings: defaultSettings });
  },
}));
