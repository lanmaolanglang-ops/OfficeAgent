import { useState, useEffect, useCallback } from 'react';
import {
  Server, Key, Bot, Bell, Save, RotateCcw, Check, Loader2,
  ExternalLink, Power, Minimize2, Monitor, Image as ImageIcon,
  CheckCircle2, AlertTriangle,
} from 'lucide-react';
import { useSettingsStore, useBackendStore } from '../../stores';
import type { AgentType } from '../../types';
import { getModelSettings, saveModelSettings, setDefaultModel, testModelConnection, getImageModelSettings, saveImageModelSettings, testImageModelConnection, getEmbeddingModelSettings, saveEmbeddingModelSettings, testEmbeddingModelConnection, type ModelSettingsStatus, type ModelConnectionTest, type ImageModelSettings, type ImageModelConnectionTest, type EmbeddingModelSettings, type EmbeddingModelConnectionTest } from '../../services/api';
import { isTauri, setAutoStart, getAutoStart } from '../../services/tauri';
import { MODEL_OPTIONS, PROVIDER_LABELS } from './modelOptions';

const agentOptions: { value: AgentType; label: string }[] = [
  { value: 'auto', label: '自动选择' },
  { value: 'word', label: 'Word Agent' },
  { value: 'ppt', label: 'PPT Agent' },
  { value: 'excel', label: 'Excel Agent' },
];

export default function SettingsPage() {
  const { settings, updateSettings, setBackendUrl, setDefaultAgent, resetSettings } = useSettingsStore();
  // Backend URL 草稿态：避免每次按键就写 localStorage 并打断进行中的轮询
  const [backendUrlDraft, setBackendUrlDraft] = useState(settings.backend_url);
  useEffect(() => { setBackendUrlDraft(settings.backend_url); }, [settings.backend_url]);
  const commitBackendUrl = () => {
    const trimmed = backendUrlDraft.trim().replace(/\/+$/, '');
    const next = trimmed || 'http://127.0.0.1:8765';
    if (next !== settings.backend_url) setBackendUrl(next);
    setBackendUrlDraft(next);
  };
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

  const handleSave = () => {
    // 设置本就随改动实时写入 localStorage；按钮的实事是提交 Backend URL 草稿
    commitBackendUrl();
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };
  const handleTestConnection = async () => { await check(); };
  const handleAutostartChange = async (enabled: boolean) => {
    if (tauriAvailable) {
      const result = await setAutoStart(enabled);
      setAutostartEnabled(result);
      updateSettings({ autostart: result });
    }
  };

  const [modelForm, setModelForm] = useState({
    provider: 'deepseek', model: MODEL_OPTIONS.deepseek[0], apiKey: '',
  });
  const [modelStatus, setModelStatus] = useState<ModelSettingsStatus>({ configured: false, models: [] });
  const [savingModel, setSavingModel] = useState(false);
  const [modelSaved, setModelSaved] = useState(false);
  const [modelError, setModelError] = useState('');
  const [testingModelId, setTestingModelId] = useState<string | null>(null);
  const [modelTests, setModelTests] = useState<Record<string, ModelConnectionTest>>({});

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

  const handleModelTest = async (modelId: string) => {
    setTestingModelId(modelId);
    setModelError('');
    try {
      const result = await testModelConnection(modelId);
      setModelTests((tests) => ({ ...tests, [modelId]: result }));
    } catch (e) {
      setModelError(e instanceof Error ? e.message : '连接测试失败');
    } finally {
      setTestingModelId(null);
    }
  };

  const [imageForm, setImageForm] = useState({ provider: 'agnes', model: '', apiKey: '', baseUrl: '', mcpUrl: '' });
  const [imageStatus, setImageStatus] = useState<ImageModelSettings>({ configured: false, provider: 'agnes', model: '', base_url: '', mcp_url: '', api_key_mask: '' });
  const [savingImage, setSavingImage] = useState(false);
  const [testingImage, setTestingImage] = useState(false);
  const [imageError, setImageError] = useState('');
  const [imageTest, setImageTest] = useState<ImageModelConnectionTest | null>(null);

  const loadImageStatus = useCallback(async () => {
    try {
      const status = await getImageModelSettings();
      setImageStatus(status);
      setImageForm((f) => ({ ...f, provider: status.provider || f.provider, model: status.model || f.model, baseUrl: status.base_url || f.baseUrl, mcpUrl: status.mcp_url || f.mcpUrl }));
    } catch {
      // Backend 未连接时保持默认状态
    }
  }, []);

  useEffect(() => {
    loadImageStatus();
  }, [loadImageStatus]);

  const handleSaveImage = async () => {
    if (imageForm.provider !== 'mcp' && !imageForm.apiKey.trim() && !imageStatus.configured) {
      setImageError('请输入生图 API Key（通常可与语言模型共用）');
      return;
    }
    setSavingImage(true);
    setImageError('');
    setImageTest(null);
    try {
      const status = await saveImageModelSettings({
        provider: imageForm.provider,
        model: imageForm.model.trim(),
        api_key: imageForm.apiKey.trim(),
        base_url: imageForm.baseUrl.trim(),
        mcp_url: imageForm.mcpUrl.trim(),
      });
      setImageStatus(status);
      setImageForm((f) => ({ ...f, apiKey: '' }));
    } catch (e) {
      setImageError(e instanceof Error ? e.message : '保存失败，请检查 Backend 连接');
    } finally {
      setSavingImage(false);
    }
  };

  const handleImageTest = async () => {
    setTestingImage(true);
    setImageError('');
    setImageTest(null);
    try {
      setImageTest(await testImageModelConnection());
    } catch (e) {
      setImageError(e instanceof Error ? e.message : '生图测试失败');
    } finally {
      setTestingImage(false);
    }
  };

  const [embeddingForm, setEmbeddingForm] = useState({ model: '', baseUrl: '', apiKey: '' });
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingModelSettings>({
    configured: false, provider: 'custom', model: '', base_url: '', api_key_mask: '',
  });
  const [savingEmbedding, setSavingEmbedding] = useState(false);
  const [testingEmbedding, setTestingEmbedding] = useState(false);
  const [embeddingError, setEmbeddingError] = useState('');
  const [embeddingTest, setEmbeddingTest] = useState<EmbeddingModelConnectionTest | null>(null);

  const loadEmbeddingStatus = useCallback(async () => {
    try {
      const status = await getEmbeddingModelSettings();
      setEmbeddingStatus(status);
      setEmbeddingForm((f) => ({
        ...f,
        model: status.model || f.model,
        baseUrl: status.base_url || f.baseUrl,
      }));
    } catch {
      // Backend 未连接时保持默认状态
    }
  }, []);

  useEffect(() => {
    loadEmbeddingStatus();
  }, [loadEmbeddingStatus]);

  const handleSaveEmbedding = async () => {
    if (!embeddingForm.apiKey.trim() && !embeddingStatus.configured) {
      setEmbeddingError('请先填写 Embedding API Key');
      return;
    }
    setSavingEmbedding(true);
    setEmbeddingError('');
    setEmbeddingTest(null);
    try {
      const status = await saveEmbeddingModelSettings({
        provider: 'custom',
        model: embeddingForm.model.trim(),
        api_key: embeddingForm.apiKey.trim(),
        base_url: embeddingForm.baseUrl.trim(),
      });
      setEmbeddingStatus(status);
      setEmbeddingForm((f) => ({ ...f, apiKey: '' }));
    } catch (e) {
      setEmbeddingError(e instanceof Error ? e.message : '保存失败，请检查 Backend 连接');
    } finally {
      setSavingEmbedding(false);
    }
  };

  const handleEmbeddingTest = async () => {
    setTestingEmbedding(true);
    setEmbeddingError('');
    setEmbeddingTest(null);
    try {
      setEmbeddingTest(await testEmbeddingModelConnection());
    } catch (e) {
      setEmbeddingError(e instanceof Error ? e.message : 'Embedding 测试失败');
    } finally {
      setTestingEmbedding(false);
    }
  };

  const inputCls = "form-control";
  const cardCls = "settings-section";
  const labelCls = "text-sm font-medium text-fg-soft mb-3 flex items-center gap-2";

  return (
    <div className="flex flex-col h-full bg-bg">
      <div className="page-header flex-shrink-0">
        <h1 className="text-fg">设置</h1>
        <p className="text-sm text-fg-soft mt-1">配置 Backend 连接和应用偏好</p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-2xl space-y-5">
          {/* Backend */}
          <div className={cardCls}>
            <h2 className={labelCls}><Server className="w-4 h-4 text-fg-soft" /> Backend 连接</h2>
            <div className="flex gap-2">
              <label htmlFor="backend-url" className="sr-only">Backend URL</label>
              <input id="backend-url" type="url" value={backendUrlDraft} onChange={(e) => setBackendUrlDraft(e.target.value)} onBlur={commitBackendUrl} onKeyDown={(e) => { if (e.key === 'Enter') { e.currentTarget.blur(); } }} className={inputCls} placeholder="http://127.0.0.1:8765" />
              <button onClick={handleTestConnection} className="px-4 py-2.5 bg-muted text-fg-soft rounded-xl hover:bg-muted text-sm transition-colors flex items-center gap-1.5 flex-shrink-0">
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
            <label htmlFor="default-agent" className={labelCls}><Bot className="w-4 h-4 text-fg-soft" /> 默认 Agent</label>
            <select id="default-agent" value={settings.default_agent} onChange={(e) => setDefaultAgent(e.target.value as AgentType)} className={inputCls}>
              {agentOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </div>

          {/* Desktop */}
          {tauriAvailable && (
            <div className={cardCls}>
              <h2 className={labelCls}><Monitor className="w-4 h-4 text-fg-soft" /> 桌面应用设置</h2>
              <div className="space-y-4">
                <label className="flex items-center justify-between cursor-pointer">
                  <div className="flex items-center gap-2.5">
                    <Power className="w-4 h-4 text-fg-soft" />
                    <div>
                      <span className="text-sm text-fg-soft">开机自动启动</span>
                      <p className="text-xs text-fg-muted">系统启动时自动运行</p>
                    </div>
                  </div>
                  <input type="checkbox" checked={autostartEnabled} onChange={(e) => handleAutostartChange(e.target.checked)} className="w-4 h-4 accent-brand" />
                </label>
                <label className="flex items-center justify-between cursor-pointer">
                  <div className="flex items-center gap-2.5">
                    <Minimize2 className="w-4 h-4 text-fg-soft" />
                    <div>
                      <span className="text-sm text-fg-soft">关闭时最小化到托盘</span>
                      <p className="text-xs text-fg-muted">关闭按钮不退出程序</p>
                    </div>
                  </div>
                  <input type="checkbox" checked={settings.minimize_to_tray} onChange={(e) => updateSettings({ minimize_to_tray: e.target.checked })} className="w-4 h-4 accent-brand" />
                </label>
              </div>
            </div>
          )}

          {/* AI 模型配置 */}
          <div className={cardCls}>
            <h2 className={labelCls}><Key className="w-4 h-4 text-fg-soft" /> AI 模型配置</h2>

            {modelStatus.models.length > 0 ? (
              <div className="mb-4 space-y-2">
                <p className="text-xs text-fg-soft">已保存的模型 · 点击“设为默认”即可切换，无需重新输入 Key</p>
                {modelStatus.models.map((m) => (
                  <div key={m.id} className={`rounded-xl px-3 py-2.5 border ${m.is_default ? 'border-brand/50 bg-brand-soft' : 'border-line bg-card'}`}>
                    <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm text-fg flex items-center gap-2">
                        {m.display_name || m.provider}
                        {m.is_default && <span className="text-xs px-1.5 py-0.5 rounded bg-brand-soft text-brand">默认</span>}
                      </p>
                      <p className="text-xs text-fg-soft truncate">{m.model} · {m.api_key_mask}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button onClick={() => handleModelTest(m.id)} disabled={testingModelId !== null} className="px-3 py-1.5 text-xs bg-muted text-fg rounded-lg hover:bg-line transition-colors disabled:opacity-50" aria-label={`测试 ${m.display_name || m.provider} 连接`}>
                        {testingModelId === m.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '测试'}
                      </button>
                      {!m.is_default && (
                      <button onClick={() => handleSwitchDefault(m.id)} className="px-3 py-1.5 text-xs bg-muted text-fg rounded-lg hover:bg-muted transition-colors flex-shrink-0">
                        设为默认
                      </button>
                      )}
                    </div>
                    </div>
                    {modelTests[m.id] && (
                      <p role="status" className={`mt-2 text-xs ${modelTests[m.id].success ? 'text-ok' : 'text-danger'}`}>
                        {modelTests[m.id].message}{modelTests[m.id].success ? ` · ${modelTests[m.id].latency_ms}ms` : ''}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mb-4">
                <span className="text-sm text-fg-muted">⚪ 未配置，当前使用模板模式</span>
              </div>
            )}

            <div className="border-t border-line pt-4 space-y-3">
              <p className="text-xs text-fg-soft">添加新模型</p>
              <div>
                <label htmlFor="model-provider" className="block text-xs text-fg-muted mb-1.5">供应商</label>
                <select id="model-provider" value={modelForm.provider} onChange={(e) => {
                  const nextProvider = e.target.value;
                  const nextModels = MODEL_OPTIONS[nextProvider] || [];
                  // 供应商与模型必须联动：切换供应商后重置为该供应商的模型，
                  // 不能把上一个供应商的 model 一起提交（M36③）。
                  setModelForm((f) => ({ ...f, provider: nextProvider, model: nextModels[0] ?? '' }));
                }} className={inputCls}>
                  {Object.keys(MODEL_OPTIONS).map((provider) => (
                    <option key={provider} value={provider}>{PROVIDER_LABELS[provider] || provider}</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="model-name" className="block text-xs text-fg-muted mb-1.5">模型</label>
                <select id="model-name" value={modelForm.model} onChange={(e) => setModelForm((f) => ({ ...f, model: e.target.value }))} className={inputCls}>
                  {(MODEL_OPTIONS[modelForm.provider] || []).map((m) => <option key={m} value={m}>{m}</option>)}
                  {modelForm.model && !(MODEL_OPTIONS[modelForm.provider] || []).includes(modelForm.model) && (
                    <option value={modelForm.model}>{modelForm.model}（自定义）</option>
                  )}
                </select>
              </div>
              <div>
                <label htmlFor="model-api-key" className="block text-xs text-fg-muted mb-1.5">API Key</label>
                <input id="model-api-key" type="password" value={modelForm.apiKey} onChange={(e) => setModelForm((f) => ({ ...f, apiKey: e.target.value }))} className={inputCls} placeholder="sk-..." autoComplete="off" />
              </div>
              <div className="flex items-center gap-3">
                <button onClick={handleSaveModel} disabled={savingModel} className="flex items-center gap-2 px-4 py-2 bg-muted text-fg rounded-xl hover:bg-muted text-sm transition-colors disabled:opacity-50">
                  {savingModel ? '保存中...' : '保存配置'}
                </button>
                {modelSaved && <span className="text-sm text-green-400">已保存</span>}
                {modelError && <span role="alert" className="text-sm text-red-400">{modelError}</span>}
              </div>
            </div>
            <p className="text-xs text-fg-muted mt-3">API Key 仅加密保存在本机应用数据目录，调用时直接发送给所选模型服务，不写入任务记录。</p>
          </div>

          {/* 生图模型 */}
          <div className={cardCls}>
            <h2 className={labelCls}><ImageIcon className="w-4 h-4 text-fg-soft" /> PPT 配图模型</h2>
            <div className="mb-3">
              {imageStatus.configured ? (
                <span className="flex items-center gap-2 text-sm text-ok"><CheckCircle2 className="h-4 w-4" aria-hidden="true" />已配置（{imageStatus.provider} / {imageStatus.model || '默认模型'}）</span>
              ) : (
                <span className="flex items-center gap-2 text-sm text-fg-muted"><AlertTriangle className="h-4 w-4" aria-hidden="true" />未配置，PPT 生成时不配图</span>
              )}
            </div>
            <div className="space-y-3">
              <div>
                <label htmlFor="image-provider" className="block text-xs text-fg-muted mb-1.5">生图服务</label>
                <select id="image-provider" value={imageForm.provider} onChange={(e) => setImageForm((f) => ({ ...f, provider: e.target.value }))} className={inputCls}>
                  <option value="agnes">Agnes 生图（OpenAI 兼容 /images/generations）</option>
                  <option value="mcp">MCP 网关</option>
                </select>
              </div>
              {imageForm.provider === 'mcp' ? (
                <div>
                  <label htmlFor="image-mcp-url" className="block text-xs text-fg-muted mb-1.5">MCP 网关地址</label>
                  <input id="image-mcp-url" type="url" value={imageForm.mcpUrl} onChange={(e) => setImageForm((f) => ({ ...f, mcpUrl: e.target.value }))} className={inputCls} placeholder="https://your-mcp-host" />
                </div>
              ) : (
                <>
                  <div>
                    <label htmlFor="image-model" className="block text-xs text-fg-muted mb-1.5">生图模型名</label>
                    <input id="image-model" type="text" value={imageForm.model} onChange={(e) => setImageForm((f) => ({ ...f, model: e.target.value }))} className={inputCls} placeholder="如 agnes-image-2.0-flash" />
                  </div>
                  <div>
                    <label htmlFor="image-base-url" className="block text-xs text-fg-muted mb-1.5">Base URL（可选，默认 Agnes）</label>
                    <input id="image-base-url" type="url" value={imageForm.baseUrl} onChange={(e) => setImageForm((f) => ({ ...f, baseUrl: e.target.value }))} className={inputCls} placeholder="https://apihub.agnes-ai.com/v1" />
                  </div>
                </>
              )}
              <div>
                <label htmlFor="image-api-key" className="block text-xs text-fg-muted mb-1.5">生图 API Key{imageStatus.configured ? '（留空则保留已保存的 Key）' : ''}</label>
                <input id="image-api-key" type="password" value={imageForm.apiKey} onChange={(e) => setImageForm((f) => ({ ...f, apiKey: e.target.value }))} className={inputCls} placeholder="通常可与语言模型共用同一个 Key" autoComplete="off" aria-describedby="image-key-help image-config-error" />
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button onClick={handleSaveImage} disabled={savingImage || testingImage} className="flex min-h-10 items-center gap-2 px-4 py-2 bg-muted text-fg rounded-xl hover:bg-line text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50">
                  {savingImage && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{savingImage ? '保存中...' : '保存生图配置'}
                </button>
                <button onClick={handleImageTest} disabled={!imageStatus.configured || savingImage || testingImage} className="flex min-h-10 items-center gap-2 px-4 py-2 bg-brand text-white rounded-xl hover:bg-brand-hover text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50">
                  {testingImage && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{testingImage ? '正在生成测试图...' : '测试生图'}
                </button>
              </div>
              {imageTest && (
                <p role="status" className={`flex items-start gap-2 text-sm ${imageTest.success ? 'text-ok' : 'text-danger'}`}>
                  {imageTest.success ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" /> : <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />}
                  <span>{imageTest.message}{imageTest.success ? ` · ${imageTest.latency_ms}ms` : ''}</span>
                </p>
              )}
              {imageError && <p id="image-config-error" role="alert" className="flex items-start gap-2 text-sm text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" /><span>{imageError}</span></p>}
              <p id="image-key-help" className="text-xs text-fg-muted">PPT 生成时会优先为图文页配图，并为普通内容页兜底。测试会真实生成并立即删除一张图片，可能产生一次模型调用费用。</p>
            </div>
          </div>

          {/* Embedding 模型 */}
          <div className={cardCls}>
            <h2 className={labelCls}><Key className="w-4 h-4 text-fg-soft" /> Embedding 模型</h2>
            <div className="mb-3">
              {embeddingStatus.configured ? (
                <span className="flex items-center gap-2 text-sm text-ok"><CheckCircle2 className="h-4 w-4" aria-hidden="true" />已配置（{embeddingStatus.model || '默认模型'} · {embeddingStatus.api_key_mask}）</span>
              ) : (
                <span className="flex items-center gap-2 text-sm text-fg-muted"><AlertTriangle className="h-4 w-4" aria-hidden="true" />未配置，RAG 知识库语义检索不可用</span>
              )}
            </div>
            <div className="space-y-3">
              <div>
                <label htmlFor="embedding-base-url" className="block text-xs text-fg-muted mb-1.5">Embedding Base URL</label>
                <input id="embedding-base-url" type="url" value={embeddingForm.baseUrl} onChange={(e) => setEmbeddingForm((f) => ({ ...f, baseUrl: e.target.value }))} className={inputCls} placeholder="https://api.openai.com/v1" />
              </div>
              <div>
                <label htmlFor="embedding-model" className="block text-xs text-fg-muted mb-1.5">Embedding Model</label>
                <input id="embedding-model" type="text" value={embeddingForm.model} onChange={(e) => setEmbeddingForm((f) => ({ ...f, model: e.target.value }))} className={inputCls} placeholder="text-embedding-3-small" />
              </div>
              <div>
                <label htmlFor="embedding-api-key" className="block text-xs text-fg-muted mb-1.5">API Key{embeddingStatus.configured ? '（留空则保留已保存的 Key）' : ''}</label>
                <input id="embedding-api-key" type="password" value={embeddingForm.apiKey} onChange={(e) => setEmbeddingForm((f) => ({ ...f, apiKey: e.target.value }))} className={inputCls} placeholder="sk-..." autoComplete="off" aria-describedby="embedding-key-help embedding-config-error" />
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button onClick={handleSaveEmbedding} disabled={savingEmbedding || testingEmbedding} className="flex min-h-10 items-center gap-2 px-4 py-2 bg-muted text-fg rounded-xl hover:bg-line text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50">
                  {savingEmbedding && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{savingEmbedding ? '保存中...' : '保存 Embedding 配置'}
                </button>
                <button onClick={handleEmbeddingTest} disabled={!embeddingStatus.configured || savingEmbedding || testingEmbedding} className="flex min-h-10 items-center gap-2 px-4 py-2 bg-brand text-white rounded-xl hover:bg-brand-hover text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50">
                  {testingEmbedding && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{testingEmbedding ? '正在测试...' : '测试连接'}
                </button>
              </div>
              {embeddingTest && (
                <p role="status" className={`flex items-start gap-2 text-sm ${embeddingTest.success ? 'text-ok' : 'text-danger'}`}>
                  {embeddingTest.success ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" /> : <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />}
                  <span>{embeddingTest.message}</span>
                </p>
              )}
              {embeddingError && <p id="embedding-config-error" role="alert" className="flex items-start gap-2 text-sm text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" /><span>{embeddingError}</span></p>}
              <p id="embedding-key-help" className="text-xs text-fg-muted">RAG 知识库语义检索使用 OpenAI-compatible Embedding Provider。API Key 仅加密保存在本机，设置页不回显完整 Key。</p>
            </div>
          </div>

          {/* Notifications */}
          <div className={cardCls}>
            <h2 className={labelCls}><Bell className="w-4 h-4 text-fg-soft" /> 通知与行为</h2>
            <div className="space-y-4">
              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-sm text-fg-soft">完成后自动打开结果文件</span>
                <input type="checkbox" checked={settings.auto_open_results} onChange={(e) => updateSettings({ auto_open_results: e.target.checked })} className="w-4 h-4 accent-brand" />
              </label>
              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-sm text-fg-soft">启用系统通知</span>
                <input type="checkbox" checked={settings.notifications} onChange={(e) => updateSettings({ notifications: e.target.checked })} className="w-4 h-4 accent-brand" />
              </label>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pb-6">
            <button onClick={handleSave} className="flex items-center gap-2 px-5 py-2.5 bg-brand text-white rounded-xl hover:bg-brand-hover text-sm transition-colors active:scale-[0.98]">
              {saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
              {saved ? '已保存' : '保存设置'}
            </button>
            <button onClick={resetSettings} className="flex items-center gap-2 px-4 py-2.5 text-fg-muted hover:text-fg hover:bg-muted rounded-xl text-sm transition-colors">
              <RotateCcw className="w-4 h-4" />恢复默认
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
