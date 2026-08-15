import { useState, useEffect, useCallback } from 'react';
import {
  Server, Key, Bot, Bell, Save, RotateCcw, Check,
  ExternalLink, Power, Minimize2, Monitor,
} from 'lucide-react';
import { useSettingsStore, useBackendStore } from '../../stores';
import type { AgentType } from '../../types';
import { getModelSettings, saveModelSettings, setDefaultModel, type ModelSettingsStatus } from '../../services/api';
import { isTauri, setAutoStart, getAutoStart } from '../../services/tauri';

const agentOptions: { value: AgentType; label: string }[] = [
  { value: 'auto', label: '自动选择' },
  { value: 'word', label: 'Word Agent' },
  { value: 'ppt', label: 'PPT Agent' },
  { value: 'excel', label: 'Excel Agent' },
];
const MODEL_OPTIONS: Record<string, string[]> = {
  openai: ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'],
  deepseek: ['deepseek-v4-pro', 'deepseek-v4-flash'],
  doubao: ['doubao-seed-2-1-pro', 'doubao-seed-2-1-turbo',
    'doubao-seed-2-0-pro', 'doubao-seed-2-0-lite', 'doubao-seed-2-0-mini', 'doubao-seed-2-0-code'],
  qwen: ['qwen3.8-max-preview', 'qwen3.7-max', 'qwen3.7-plus', 'qwen3.7-flash'],
  agnes: ['agnes-2.0-flash', 'agnes-2.5-flash', 'agnes-1.5-flash'],
};

export default function SettingsPage() {
  const { settings, updateSettings, setBackendUrl, setDefaultAgent, resetSettings } = useSettingsStore();
  const { check, connected } = useBackendStore();
  const [saved, setSaved] = useState(false);
  const [autostartEnabled, setAutostartEnabled] = useState(false);
  const [tauriAvailable, setTauriAvailable] = useState(false);

  useEffect(() => {
    if (isTauri()) {
      setTauriAvailable(true);
      getAutoStart().then(setAutostartEnabled);
    }
  }, []);

  const handleSave = () => { setSaved(true); setTimeout(() => setSaved(false), 2000); };
  const handleTestConnection = async () => { await check(); };
  const handleAutostartChange = async (enabled: boolean) => {
    if (tauriAvailable) { const result = await setAutoStart(enabled); setAutostartEnabled(result); }
    updateSettings({ autostart: enabled });
  };

  const [modelForm, setModelForm] = useState({ provider: 'deepseek', model: '', apiKey: '' });
  const [modelStatus, setModelStatus] = useState<ModelSettingsStatus>({ configured: false, models: [] });
  const [savingModel, setSavingModel] = useState(false);
  const [modelSaved, setModelSaved] = useState(false);
  const [modelError, setModelError] = useState('');

  const loadModelStatus = useCallback(async () => {
    try {
      const status = await getModelSettings();
      setModelStatus(status);
    } catch {
      // Backend 未连接时保持默认状态
    }
  }, []);

  useEffect(() => {
    loadModelStatus();
  }, [loadModelStatus]);

  const handleSaveModel = async () => {
    const apiKey = modelForm.apiKey.trim();
    if (!apiKey) {
      setModelError('请输入 API Key');
      return;
    }
    setSavingModel(true);
    setModelError('');
    try {
      await saveModelSettings({
        provider: modelForm.provider,
        model: modelForm.model.trim(),
        api_key: apiKey,
      });
      setModelForm((f) => ({ ...f, apiKey: '' }));
      setModelSaved(true);
      setTimeout(() => setModelSaved(false), 2000);
      await loadModelStatus(); // 刷新已保存模型列表
    } catch (e) {
      setModelError(e instanceof Error ? e.message : '保存失败，请检查 Backend 连接');
    } finally {
      setSavingModel(false);
    }
  };

  const handleSwitchDefault = async (modelId: string) => {
    setModelError('');
    try {
      const status = await setDefaultModel(modelId);
      setModelStatus(status);
    } catch (e) {
      setModelError(e instanceof Error ? e.message : '切换失败，请检查 Backend 连接');
    }
  };

  const inputCls = "w-full bg-[#181818] border border-[#2A2A2A] rounded-xl px-3 py-2.5 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500/50 transition-colors";
  const cardCls = "bg-[#181818] rounded-2xl border border-[#2A2A2A] p-5";
  const labelCls = "text-sm font-medium text-gray-300 mb-3 flex items-center gap-2";

  return (
    <div className="flex flex-col h-full bg-[#111111]">
      <div className="px-8 py-6 flex-shrink-0">
        <h1 className="text-2xl font-semibold text-white tracking-tight">设置</h1>
        <p className="text-sm text-gray-500 mt-1">配置 Backend 连接和应用偏好</p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-2xl space-y-5">
          {/* Backend */}
          <div className={cardCls}>
            <h2 className={labelCls}><Server className="w-4 h-4 text-gray-500" /> Backend 连接</h2>
            <div className="flex gap-2">
              <input type="text" value={settings.backend_url} onChange={(e) => setBackendUrl(e.target.value)} className={inputCls} placeholder="http://127.0.0.1:8765" />
              <button onClick={handleTestConnection} className="px-4 py-2.5 bg-[#222222] text-gray-300 rounded-xl hover:bg-[#2a2a2a] text-sm transition-colors flex items-center gap-1.5 flex-shrink-0">
                {connected ? <Check className="w-4 h-4 text-green-400" /> : <ExternalLink className="w-4 h-4" />}
                测试
              </button>
            </div>
            <p className="text-xs mt-2">
              {connected ? <span className="text-green-400">● Backend 已连接</span> : <span className="text-red-400">● 未连接</span>}
            </p>
          </div>

          {/* Default Agent */}
          <div className={cardCls}>
            <h2 className={labelCls}><Bot className="w-4 h-4 text-gray-500" /> 默认 Agent</h2>
            <select value={settings.default_agent} onChange={(e) => setDefaultAgent(e.target.value as AgentType)} className={inputCls}>
              {agentOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </div>

          {/* Desktop */}
          {tauriAvailable && (
            <div className={cardCls}>
              <h2 className={labelCls}><Monitor className="w-4 h-4 text-gray-500" /> 桌面应用设置</h2>
              <div className="space-y-4">
                <label className="flex items-center justify-between cursor-pointer">
                  <div className="flex items-center gap-2.5">
                    <Power className="w-4 h-4 text-gray-500" />
                    <div>
                      <span className="text-sm text-gray-300">开机自动启动</span>
                      <p className="text-xs text-gray-600">系统启动时自动运行</p>
                    </div>
                  </div>
                  <input type="checkbox" checked={autostartEnabled} onChange={(e) => handleAutostartChange(e.target.checked)} className="w-4 h-4 accent-indigo-500" />
                </label>
                <label className="flex items-center justify-between cursor-pointer">
                  <div className="flex items-center gap-2.5">
                    <Minimize2 className="w-4 h-4 text-gray-500" />
                    <div>
                      <span className="text-sm text-gray-300">关闭时最小化到托盘</span>
                      <p className="text-xs text-gray-600">关闭按钮不退出程序</p>
                    </div>
                  </div>
                  <input type="checkbox" checked={settings.minimize_to_tray} onChange={(e) => updateSettings({ minimize_to_tray: e.target.checked })} className="w-4 h-4 accent-indigo-500" />
                </label>
              </div>
            </div>
          )}

          {/* AI 模型配置 */}
          <div className={cardCls}>
            <h2 className={labelCls}><Key className="w-4 h-4 text-gray-500" /> AI 模型配置</h2>

            {modelStatus.models.length > 0 ? (
              <div className="mb-4 space-y-2">
                <p className="text-xs text-gray-500">已保存的模型 · 点击“设为默认”即可切换，无需重新输入 Key</p>
                {modelStatus.models.map((m) => (
                  <div key={m.id} className={`flex items-center justify-between rounded-xl px-3 py-2.5 border ${m.is_default ? 'border-indigo-500/50 bg-indigo-500/10' : 'border-[#2A2A2A] bg-[#1b1b1b]'}`}>
                    <div className="min-w-0">
                      <p className="text-sm text-gray-200 flex items-center gap-2">
                        {m.display_name || m.provider}
                        {m.is_default && <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300">默认</span>}
                      </p>
                      <p className="text-xs text-gray-500 truncate">{m.model} · {m.api_key_mask}</p>
                    </div>
                    {!m.is_default && (
                      <button onClick={() => handleSwitchDefault(m.id)} className="px-3 py-1.5 text-xs bg-[#222222] text-gray-200 rounded-lg hover:bg-[#2a2a2a] transition-colors flex-shrink-0">
                        设为默认
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mb-4">
                <span className="text-sm text-gray-400">⚪ 未配置，当前使用模板模式</span>
              </div>
            )}

            <div className="border-t border-[#2A2A2A] pt-4 space-y-3">
              <p className="text-xs text-gray-500">添加新模型</p>
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">供应商</label>
                <select value={modelForm.provider} onChange={(e) => {
                  const nextProvider = e.target.value;
                  const nextModels = MODEL_OPTIONS[nextProvider] || [];
                  setModelForm((f) => ({ ...f, provider: nextProvider, model: nextModels.length ? nextModels[0] : f.model }));
                }} className={inputCls}>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai">OpenAI</option>
                  <option value="doubao">豆包</option>
                  <option value="qwen">通义千问</option>
                  <option value="agnes">Agnes AI</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">模型</label>
                <select value={modelForm.model} onChange={(e) => setModelForm((f) => ({ ...f, model: e.target.value }))} className={inputCls}>
                  {(MODEL_OPTIONS[modelForm.provider] || []).map((m) => <option key={m} value={m}>{m}</option>)}
                  {modelForm.model && !(MODEL_OPTIONS[modelForm.provider] || []).includes(modelForm.model) && (
                    <option value={modelForm.model}>{modelForm.model}（自定义）</option>
                  )}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">API Key</label>
                <input type="password" value={modelForm.apiKey} onChange={(e) => setModelForm((f) => ({ ...f, apiKey: e.target.value }))} className={inputCls} placeholder="sk-..." autoComplete="off" />
              </div>
              <div className="flex items-center gap-3">
                <button onClick={handleSaveModel} disabled={savingModel} className="flex items-center gap-2 px-4 py-2 bg-[#222222] text-gray-200 rounded-xl hover:bg-[#2a2a2a] text-sm transition-colors disabled:opacity-50">
                  {savingModel ? '保存中...' : '保存配置'}
                </button>
                {modelSaved && <span className="text-sm text-green-400">已保存</span>}
                {modelError && <span className="text-sm text-red-400">{modelError}</span>}
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-3">API Key 仅保存在本机 ~/.office_agent/models.json，不会上传云端或写入任务记录。</p>
          </div>

          {/* Notifications */}
          <div className={cardCls}>
            <h2 className={labelCls}><Bell className="w-4 h-4 text-gray-500" /> 通知与行为</h2>
            <div className="space-y-4">
              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-sm text-gray-300">完成后自动打开结果文件</span>
                <input type="checkbox" checked={settings.auto_open_results} onChange={(e) => updateSettings({ auto_open_results: e.target.checked })} className="w-4 h-4 accent-indigo-500" />
              </label>
              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-sm text-gray-300">启用系统通知</span>
                <input type="checkbox" checked={settings.notifications} onChange={(e) => updateSettings({ notifications: e.target.checked })} className="w-4 h-4 accent-indigo-500" />
              </label>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pb-6">
            <button onClick={handleSave} className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-indigo-500 to-purple-600 text-white rounded-xl hover:shadow-lg hover:shadow-indigo-500/25 text-sm transition-all active:scale-95">
              {saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
              {saved ? '已保存' : '保存设置'}
            </button>
            <button onClick={resetSettings} className="flex items-center gap-2 px-4 py-2.5 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded-xl text-sm transition-colors">
              <RotateCcw className="w-4 h-4" />恢复默认
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
