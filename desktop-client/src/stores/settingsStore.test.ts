/**
 * P5-4（审计 M36③）：设置持久化与 canonical 来源回归。
 *
 * 修复前：
 * - `loadSettings` 对 `JSON.parse` 结果不做类型校验——持久化值是数组时
 *   `{...defaultSettings, ...parsed}` 会展开出 "0"/"1" 之类的伪键；
 * - store 里硬编码一份前端模型清单（含后端不认识的虚构版本号，provider
 *   名也不是后端 canonical 的 `claude`），与后端 default_model_catalog 双源；
 * - Settings 页的供应商下拉缺少 Claude，且切换供应商会保留上一个供应商的
 *   model，提交出错误的 provider/model 组合。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { parsePersistedSettings, useSettingsStore } from './settingsStore';
import { PROVIDER_LABELS } from '../pages/Settings/modelOptions';

describe('parsePersistedSettings（持久化数据是不可信输入）', () => {
  it('数组不得展开成 "0"/"1" 伪键', () => {
    const settings = parsePersistedSettings(JSON.stringify([{ provider: 'x' }]));
    expect(Object.keys(settings)).not.toContain('0');
    expect(settings.backend_url).toBe('http://127.0.0.1:8765');
    expect(settings.models).toEqual([]);
  });

  it('null / 非对象 / 非法 JSON 一律回退默认值', () => {
    for (const raw of ['null', '42', '"text"', '{not json', '']) {
      const settings = parsePersistedSettings(raw);
      expect(settings.backend_url).toBe('http://127.0.0.1:8765');
      expect(settings.default_agent).toBe('auto');
      expect(settings.models).toEqual([]);
    }
    expect(parsePersistedSettings(null).default_agent).toBe('auto');
  });

  it('字段类型错位时逐项回退，不污染其它字段', () => {
    const settings = parsePersistedSettings(JSON.stringify({
      backend_url: 123,
      default_agent: 'not-an-agent',
      auto_open_results: 'yes',
      notifications: null,
      autostart: 1,
      minimize_to_tray: 'true',
      models: 'not-an-array',
    }));
    expect(settings.backend_url).toBe('http://127.0.0.1:8765');
    expect(settings.default_agent).toBe('auto');
    expect(settings.auto_open_results).toBe(true);
    expect(settings.notifications).toBe(true);
    expect(settings.autostart).toBe(false);
    expect(settings.minimize_to_tray).toBe(true);
    expect(settings.models).toEqual([]);
  });

  it('合法的 default_agent 被保留', () => {
    expect(parsePersistedSettings(JSON.stringify({ default_agent: 'ppt' })).default_agent).toBe('ppt');
  });

  it('models 里的非对象条目被丢弃，API Key 一律清空（secret 不落 localStorage）', () => {
    const settings = parsePersistedSettings(JSON.stringify({
      models: [
        { provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-secret' },
        'garbage',
        null,
        [{ nested: true }],
      ],
    }));
    expect(settings.models).toHaveLength(1);
    expect(settings.models[0].api_key).toBe('');
    expect(JSON.stringify(settings)).not.toContain('sk-secret');
  });

  it('旧版本模型名仍做一次性升级', () => {
    const settings = parsePersistedSettings(JSON.stringify({
      models: [{ provider: 'openai', model: 'gpt-5.4', api_key: '' }],
    }));
    expect(settings.models[0].model).toBe('gpt-5.6-sol');
  });

  it('不再播种前端硬编码模型清单（模型清单以后端为准）', () => {
    expect(parsePersistedSettings(null).models).toEqual([]);
    // 也不得出现后端 canonical 之外的 provider 名（如 `anthropic`）
    const providers = parsePersistedSettings(null).models.map((m) => m.provider);
    expect(providers).not.toContain('anthropic');
  });
});

describe('settingsStore 持久化语义', () => {
  beforeEach(() => {
    localStorage.clear();
    useSettingsStore.setState({ settings: parsePersistedSettings(null) });
  });

  it('updateSettings 落盘并可被重新解析（reload 一致）', () => {
    useSettingsStore.getState().updateSettings({ default_agent: 'excel', backend_url: 'http://127.0.0.1:9999' });
    const raw = localStorage.getItem('officeagent_settings');
    const reloaded = parsePersistedSettings(raw);
    expect(reloaded.default_agent).toBe('excel');
    expect(reloaded.backend_url).toBe('http://127.0.0.1:9999');
    // backend_url 同时写入独立键（请求层直接读它）
    expect(localStorage.getItem('backend_url')).toBe('http://127.0.0.1:9999');
  });

  it('保存失败不抛出，内存态仍更新', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    try {
      expect(() => useSettingsStore.getState().setDefaultAgent('word')).not.toThrow();
      expect(useSettingsStore.getState().settings.default_agent).toBe('word');
    } finally {
      spy.mockRestore();
    }
  });

  it('resetSettings 回到默认值', () => {
    useSettingsStore.getState().setDefaultAgent('word');
    useSettingsStore.getState().resetSettings();
    expect(useSettingsStore.getState().settings.default_agent).toBe('auto');
    expect(parsePersistedSettings(localStorage.getItem('officeagent_settings')).default_agent).toBe('auto');
  });
});

describe('Settings 页供应商标签', () => {
  it('包含后端 canonical 的 claude 供应商（修复前缺失，无法添加 Claude）', () => {
    expect(Object.keys(PROVIDER_LABELS)).toContain('claude');
    expect(PROVIDER_LABELS.claude).toBeTruthy();
  });

  it('不在前端维护模型版本清单，provider 别名也不进入标签表', () => {
    expect(Object.keys(PROVIDER_LABELS)).not.toContain('anthropic');
    expect(Object.values(PROVIDER_LABELS).join(' ')).not.toContain('gpt-');
  });
});
