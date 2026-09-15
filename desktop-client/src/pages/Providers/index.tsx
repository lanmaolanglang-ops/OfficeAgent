import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Image as ImageIcon, KeyRound, Loader2, Pencil, Plus, RefreshCw, Server, Trash2, X } from 'lucide-react';
import {
  deleteCustomProvider, deleteImageProvider, discoverImageModels, discoverProviderModels,
  getImageProviders, getProviderSettings, saveCustomProvider, saveImageProvider,
  setDefaultImageProvider, testCustomProvider, testImageProvider,
  type ProviderConfigItem, type ProviderProbeResult,
} from '../../services/api';

const inputCls = 'form-control';
const actionCls = 'inline-flex min-h-10 items-center justify-center gap-2 px-3 text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-50';

interface ProviderDraft {
  id: string;
  name: string;
  protocol: string;
  baseUrl: string;
  apiKey: string;
  models: string[];
  defaultModel: string;
  enabled: boolean;
  allowLocal: boolean;
  clearKey: boolean;
}

const emptyLLM = (): ProviderDraft => ({ id: '', name: '', protocol: 'openai_compatible', baseUrl: '', apiKey: '', models: [], defaultModel: '', enabled: true, allowLocal: false, clearKey: false });
const emptyImage = (): ProviderDraft => ({ id: '', name: '', protocol: 'openai_image_compatible', baseUrl: '', apiKey: '', models: [], defaultModel: '', enabled: true, allowLocal: false, clearKey: false });

function toDraft(item: ProviderConfigItem): ProviderDraft {
  return {
    id: item.id, name: item.name, protocol: item.protocol, baseUrl: item.base_url,
    apiKey: '', models: [...item.models], defaultModel: item.default_model,
    enabled: item.enabled, allowLocal: Boolean(item.allow_local_endpoint), clearKey: false,
  };
}

function StatusMessage({ result, error }: { result: ProviderProbeResult | null; error: string }) {
  if (!result && !error) return null;
  const success = Boolean(result?.success) && !error;
  return (
    <p role={success ? 'status' : 'alert'} className={`mt-3 flex items-start gap-2 text-xs ${success ? 'text-ok' : 'text-danger'}`}>
      {success ? <Check aria-hidden="true" className="mt-0.5 h-4 w-4 flex-none" /> : <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-none" />}
      {error || result?.message}
    </p>
  );
}

function ModelEditor({ draft, setDraft, busy, onDetect, onTest }: {
  draft: ProviderDraft;
  setDraft: (next: ProviderDraft) => void;
  busy: 'detect' | 'test' | 'save' | '';
  onDetect: () => void;
  onTest: () => void;
}) {
  const [manualModel, setManualModel] = useState('');
  const addManual = () => {
    const model = manualModel.trim();
    if (!model || draft.models.includes(model)) return;
    const models = [...draft.models, model];
    setDraft({ ...draft, models, defaultModel: draft.defaultModel || model });
    setManualModel('');
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onTest} disabled={Boolean(busy)} className={`${actionCls} border border-line-strong bg-card text-fg-soft hover:bg-muted`}>
          {busy === 'test' ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Server aria-hidden="true" className="h-4 w-4" />}测试连接
        </button>
        <button type="button" onClick={onDetect} disabled={Boolean(busy)} className={`${actionCls} border border-line-strong bg-card text-fg-soft hover:bg-muted`}>
          {busy === 'detect' ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <RefreshCw aria-hidden="true" className="h-4 w-4" />}检测模型
        </button>
      </div>
      <div>
        <label htmlFor={`manual-model-${draft.id || 'new'}`} className="mb-2 block text-xs font-semibold text-fg-soft">手工添加模型 ID</label>
        <div className="flex gap-2">
          <input id={`manual-model-${draft.id || 'new'}`} value={manualModel} onChange={(event) => setManualModel(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addManual(); } }} className={inputCls} placeholder="custom-model-name" />
          <button type="button" onClick={addManual} className={`${actionCls} bg-sidebar text-sidebar-strong hover:opacity-90`}>添加</button>
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs font-semibold text-fg-soft">已选模型与默认模型</p>
        {draft.models.length === 0 ? (
          <p className="border border-dashed border-line-strong px-3 py-4 text-xs text-fg-muted">尚无模型。检测失败不会阻止使用，请在上方手工添加。</p>
        ) : (
          <div className="divide-y divide-line border border-line bg-card">
            {draft.models.map((model) => (
              <div key={model} className="flex min-h-11 items-center gap-3 px-3">
                <input type="radio" name={`default-${draft.id || 'new'}`} checked={draft.defaultModel === model} onChange={() => setDraft({ ...draft, defaultModel: model })} aria-label={`将 ${model} 设为默认模型`} className="h-4 w-4 accent-brand" />
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-fg" title={model}>{model}</span>
                <button type="button" onClick={() => { const models = draft.models.filter((value) => value !== model); setDraft({ ...draft, models, defaultModel: draft.defaultModel === model ? (models[0] || '') : draft.defaultModel }); }} aria-label={`移除模型 ${model}`} className="inline-grid min-h-9 min-w-9 place-items-center text-fg-muted hover:bg-danger-soft hover:text-danger"><X aria-hidden="true" className="h-4 w-4" /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ProviderForm({ kind, initial, onCancel, onSaved }: {
  kind: 'llm' | 'image';
  initial: ProviderDraft;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState<'detect' | 'test' | 'save' | ''>('');
  const [result, setResult] = useState<ProviderProbeResult | null>(null);
  const [error, setError] = useState('');

  const probePayload = {
    provider_id: draft.id, protocol: draft.protocol, base_url: draft.baseUrl,
    api_key: draft.apiKey, model: draft.defaultModel || draft.models[0] || '',
    allow_local_endpoint: draft.allowLocal,
  };
  const detect = async () => {
    setBusy('detect'); setError(''); setResult(null);
    try {
      const response = kind === 'llm' ? await discoverProviderModels(probePayload) : await discoverImageModels(probePayload);
      const models = response.models || [];
      setDraft((current) => ({ ...current, models: Array.from(new Set([...current.models, ...models])), defaultModel: current.defaultModel || models[0] || '' }));
      setResult(response);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '模型检测失败；可以手工添加模型 ID'); }
    finally { setBusy(''); }
  };
  const test = async () => {
    setBusy('test'); setError(''); setResult(null);
    try { setResult(kind === 'llm' ? await testCustomProvider(probePayload) : await testImageProvider(probePayload)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '连接测试失败'); }
    finally { setBusy(''); }
  };
  const save = async () => {
    setBusy('save'); setError(''); setResult(null);
    try {
      const payload = {
        id: draft.id, name: draft.name, protocol: draft.protocol, base_url: draft.baseUrl,
        api_key: draft.apiKey, models: draft.models, default_model: draft.defaultModel,
        enabled: draft.enabled, allow_local_endpoint: draft.allowLocal, clear_api_key: draft.clearKey,
      };
      if (kind === 'llm') await saveCustomProvider(payload); else await saveImageProvider(payload);
      onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存失败'); }
    finally { setBusy(''); }
  };

  return (
    <section className="settings-section space-y-5" aria-label={draft.id ? '编辑 Provider' : '新增 Provider'}>
      <div className="flex items-start justify-between gap-4">
        <div><h2 className="text-base font-semibold text-fg">{draft.id ? '编辑' : '新增'} {kind === 'llm' ? 'LLM Provider' : 'Image Provider'}</h2><p className="mt-1 text-xs text-fg-muted">API Key 仅加密保存在本机；留空表示保留现有 Key。</p></div>
        <button type="button" onClick={onCancel} aria-label="关闭编辑器" className="inline-grid min-h-10 min-w-10 place-items-center text-fg-muted hover:bg-muted"><X aria-hidden="true" className="h-4 w-4" /></button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><label htmlFor={`${kind}-provider-name`} className="mb-2 block text-xs font-semibold text-fg-soft">Provider Name</label><input id={`${kind}-provider-name`} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className={inputCls} placeholder="例如：公司中转 API" /></div>
        <div><label htmlFor={`${kind}-protocol`} className="mb-2 block text-xs font-semibold text-fg-soft">Protocol</label><select id={`${kind}-protocol`} value={draft.protocol} onChange={(event) => setDraft({ ...draft, protocol: event.target.value })} className={inputCls}>{kind === 'llm' && <option value="openai_compatible">OpenAI Compatible</option>}{kind === 'llm' && <option value="anthropic_compatible">Anthropic Compatible</option>}{kind === 'image' && <option value="openai_image_compatible">OpenAI Image Compatible</option>}</select></div>
      </div>
      <div><label htmlFor={`${kind}-base-url`} className="mb-2 block text-xs font-semibold text-fg-soft">Base URL</label><input id={`${kind}-base-url`} type="url" value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} className={inputCls} placeholder="https://example.com/v1" /><p className="mt-1.5 text-xs text-fg-muted">填写到 API 根路径即可；OfficeAgent 会避免重复拼接 /v1。</p></div>
      <div><label htmlFor={`${kind}-api-key`} className="mb-2 block text-xs font-semibold text-fg-soft">API Key</label><input id={`${kind}-api-key`} type="password" autoComplete="off" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value, clearKey: false })} className={inputCls} placeholder={draft.id ? '留空以保留已保存 Key' : '输入 API Key'} /></div>
      {draft.id && <label className="flex min-h-10 items-center gap-3 text-xs text-fg-soft"><input type="checkbox" checked={draft.clearKey} onChange={(event) => setDraft({ ...draft, clearKey: event.target.checked, apiKey: event.target.checked ? '' : draft.apiKey })} className="h-4 w-4 accent-brand" />清除已保存的 API Key（启用状态下无法保存无 Key 配置）</label>}
      <label className="flex min-h-10 items-start gap-3 text-xs text-fg-soft"><input type="checkbox" checked={draft.allowLocal} onChange={(event) => setDraft({ ...draft, allowLocal: event.target.checked })} className="mt-1 h-4 w-4 accent-brand" /><span><strong className="block text-fg">允许本地端点</strong>仅在连接 Ollama、LM Studio 等明确由你控制的 localhost / 私网服务时启用；元数据和链路本地地址仍会拒绝。</span></label>
      <ModelEditor draft={draft} setDraft={setDraft} busy={busy} onDetect={() => void detect()} onTest={() => void test()} />
      <label className="flex min-h-10 items-center gap-3 text-xs text-fg-soft"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} className="h-4 w-4 accent-brand" />启用此 Provider</label>
      <StatusMessage result={result} error={error} />
      <div className="flex justify-end gap-2 border-t border-line pt-4"><button type="button" onClick={onCancel} className={`${actionCls} border border-line-strong text-fg-soft hover:bg-muted`}>取消</button><button type="button" onClick={() => void save()} disabled={Boolean(busy)} className={`${actionCls} bg-brand text-white hover:bg-brand-hover`}>{busy === 'save' ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Check aria-hidden="true" className="h-4 w-4" />}保存 Provider</button></div>
    </section>
  );
}

function ProviderRow({ item, image, onEdit, onDelete, onDefault }: { item: ProviderConfigItem; image?: boolean; onEdit?: () => void; onDelete?: () => void; onDefault?: () => void }) {
  return (
    <article className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-b-0">
      <span className={`inline-grid h-9 w-9 place-items-center ${image ? 'bg-accent-soft text-accent' : 'bg-brand-soft text-brand'}`}>{image ? <ImageIcon aria-hidden="true" className="h-4 w-4" /> : <KeyRound aria-hidden="true" className="h-4 w-4" />}</span>
      <div className="min-w-52 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-fg">{item.name}</h3>{item.is_default && <span className="bg-ok-soft px-2 py-0.5 text-[10px] font-semibold text-ok">PPT 默认</span>}<span className={`text-[10px] font-semibold ${item.enabled ? 'text-ok' : 'text-fg-muted'}`}>{item.enabled ? '已启用' : '已停用'}</span></div><p className="mt-1 truncate font-mono text-[11px] text-fg-muted" title={item.base_url}>{item.protocol} · {item.default_model || '未选模型'} · {item.api_key_mask || '无 Key'}</p></div>
      <div className="flex items-center gap-1">{onDefault && !item.is_default && <button type="button" onClick={onDefault} className="min-h-9 px-2 text-xs font-semibold text-brand hover:bg-brand-soft">设为 PPT 默认</button>}{onEdit && <button type="button" onClick={onEdit} aria-label={`编辑 ${item.name}`} className="inline-grid min-h-9 min-w-9 place-items-center text-fg-muted hover:bg-muted hover:text-brand"><Pencil aria-hidden="true" className="h-4 w-4" /></button>}{onDelete && <button type="button" onClick={onDelete} aria-label={`删除 ${item.name}`} className="inline-grid min-h-9 min-w-9 place-items-center text-fg-muted hover:bg-danger-soft hover:text-danger"><Trash2 aria-hidden="true" className="h-4 w-4" /></button>}</div>
    </article>
  );
}

export default function ProvidersPage() {
  const [tab, setTab] = useState<'llm' | 'image'>('llm');
  const [native, setNative] = useState<ProviderConfigItem[]>([]);
  const [custom, setCustom] = useState<ProviderConfigItem[]>([]);
  const [images, setImages] = useState<ProviderConfigItem[]>([]);
  const [editor, setEditor] = useState<ProviderDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const [llm, image] = await Promise.all([getProviderSettings(), getImageProviders()]); setNative(llm.native_presets); setCustom(llm.custom_providers); setImages(image.providers); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取 Provider 配置'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setEditor(null); }, [tab]);

  const remove = async (item: ProviderConfigItem, image: boolean) => {
    if (!window.confirm(`删除“${item.name}”？已保存的加密 Key 和模型配置也会删除。`)) return;
    try { if (image) await deleteImageProvider(item.id); else await deleteCustomProvider(item.id); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败'); }
  };
  const makeDefault = async (id: string) => { try { await setDefaultImageProvider(id); await load(); } catch (cause) { setError(cause instanceof Error ? cause.message : '切换失败'); } };

  return (
    <div className="flex h-full min-w-0 flex-col bg-bg">
      <header className="page-header flex flex-shrink-0 items-end justify-between gap-6"><div><h1 className="text-fg">模型与生图服务</h1><p className="mt-1 text-sm text-fg-soft">保留原生预设，同时接入你信任的兼容 API</p></div><button type="button" onClick={() => setEditor(tab === 'llm' ? emptyLLM() : emptyImage())} className={`${actionCls} bg-sidebar px-4 text-sidebar-strong hover:opacity-90`}><Plus aria-hidden="true" className="h-4 w-4" />新增 {tab === 'llm' ? 'LLM' : 'Image'} Provider</button></header>
      <main className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-5xl space-y-5">
          <div role="tablist" aria-label="Provider 类型" className="inline-flex border border-line-strong bg-card p-1"><button type="button" role="tab" aria-selected={tab === 'llm'} onClick={() => setTab('llm')} className={`min-h-10 px-4 text-xs font-semibold ${tab === 'llm' ? 'bg-sidebar text-sidebar-strong' : 'text-fg-soft hover:bg-muted'}`}>语言模型</button><button type="button" role="tab" aria-selected={tab === 'image'} onClick={() => setTab('image')} className={`min-h-10 px-4 text-xs font-semibold ${tab === 'image' ? 'bg-sidebar text-sidebar-strong' : 'text-fg-soft hover:bg-muted'}`}>PPT 生图</button></div>
          {error && <p role="alert" className="border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}
          {loading ? <p role="status" className="settings-section flex items-center gap-2 text-sm text-fg-soft"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />正在读取本机配置…</p> : tab === 'llm' ? (
            <>
              <section className="border border-line bg-card"><div className="border-b border-line px-4 py-3"><h2 className="text-sm font-semibold text-fg">原生 Provider</h2><p className="mt-1 text-xs text-fg-muted">OpenAI、DeepSeek、Claude、豆包、通义、Gemini 与 Agnes 继续由原设置页配置。</p></div>{native.map((item) => <ProviderRow key={item.id} item={item} />)}</section>
              <section className="border border-line bg-card"><div className="border-b border-line px-4 py-3"><h2 className="text-sm font-semibold text-fg">Custom Provider</h2><p className="mt-1 text-xs text-fg-muted">动态检测模型，也可手工添加未公开在 /models 的模型。</p></div>{custom.length ? custom.map((item) => <ProviderRow key={item.id} item={item} onEdit={() => setEditor(toDraft(item))} onDelete={() => void remove(item, false)} />) : <p className="px-4 py-8 text-center text-sm text-fg-muted">还没有 Custom Provider</p>}</section>
            </>
          ) : (
            <section className="border border-line bg-card"><div className="border-b border-line px-4 py-3"><h2 className="text-sm font-semibold text-fg">PPT Image Provider</h2><p className="mt-1 text-xs text-fg-muted">PPT Agent 只调用统一生图服务；关闭当前 Provider 后会保留模板图片区并报告生图降级。</p></div>{images.map((item) => <ProviderRow key={item.id} item={item} image onEdit={() => setEditor(toDraft(item))} onDelete={item.id === 'agnes' ? undefined : () => void remove(item, true)} onDefault={() => void makeDefault(item.id)} />)}</section>
          )}
          {editor && <ProviderForm key={`${tab}-${editor.id || 'new'}`} kind={tab} initial={editor} onCancel={() => setEditor(null)} onSaved={() => { setEditor(null); void load(); }} />}
        </div>
      </main>
    </div>
  );
}
