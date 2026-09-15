import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, Check, FileUp, Loader2, Pencil, Plus, Power, Trash2, X } from 'lucide-react';
import { createSkill, deleteSkill, importSkill, listSkills, updateSkill, type SkillItem, type SkillPayload } from '../../services/api';

const AGENTS = [
  { value: 'all', label: '全部 Agent' }, { value: 'word', label: 'Word' },
  { value: 'excel', label: 'Excel' }, { value: 'ppt', label: 'PPT' },
  { value: 'chat', label: 'Chat' },
];
const inputCls = 'form-control';
const emptySkill = (): SkillPayload => ({ name: '', description: '', instructions: '', target_agents: ['all'], enabled: true, priority: 100 });

function SkillEditor({ current, onClose, onSaved }: { current: SkillItem | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<SkillPayload>(current ? {
    name: current.name, description: current.description, instructions: current.instructions,
    target_agents: current.target_agents, enabled: current.enabled, priority: current.priority,
  } : emptySkill());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const toggleAgent = (agent: string) => {
    if (agent === 'all') { setForm({ ...form, target_agents: ['all'] }); return; }
    const withoutAll = form.target_agents.filter((value) => value !== 'all');
    const next = withoutAll.includes(agent) ? withoutAll.filter((value) => value !== agent) : [...withoutAll, agent];
    setForm({ ...form, target_agents: next.length ? next : ['all'] });
  };
  const save = async () => {
    setSaving(true); setError('');
    try { if (current) await updateSkill(current.id, form); else await createSkill(form); onSaved(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '保存 Skill 失败'); }
    finally { setSaving(false); }
  };
  return (
    <section className="settings-section space-y-5" aria-label={current ? '编辑 Skill' : '创建 Skill'}>
      <div className="flex items-start justify-between gap-4"><div><h2 className="text-base font-semibold text-fg">{current ? '编辑 Skill' : '创建 Skill'}</h2><p className="mt-1 text-xs text-fg-muted">Skill 是可复用指令，不执行 Shell、Python 或任意工具。</p></div><button type="button" onClick={onClose} aria-label="关闭 Skill 编辑器" className="inline-grid min-h-10 min-w-10 place-items-center text-fg-muted hover:bg-muted"><X aria-hidden="true" className="h-4 w-4" /></button></div>
      <div className="grid gap-4 md:grid-cols-2"><div><label htmlFor="skill-name" className="mb-2 block text-xs font-semibold text-fg-soft">Name</label><input id="skill-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className={inputCls} maxLength={128} placeholder="例如：极简商务 PPT" /></div><div><label htmlFor="skill-priority" className="mb-2 block text-xs font-semibold text-fg-soft">Priority</label><input id="skill-priority" type="number" min={0} max={1000} value={form.priority} onChange={(event) => setForm({ ...form, priority: Number(event.target.value) })} className={inputCls} /><p className="mt-1 text-xs text-fg-muted">数字越小越先注入。</p></div></div>
      <div><label htmlFor="skill-description" className="mb-2 block text-xs font-semibold text-fg-soft">Description</label><input id="skill-description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} className={inputCls} maxLength={2000} placeholder="说明适用场景" /></div>
      <fieldset><legend className="mb-2 text-xs font-semibold text-fg-soft">Target Agents</legend><div className="flex flex-wrap gap-2">{AGENTS.map((agent) => <label key={agent.value} className={`inline-flex min-h-10 items-center gap-2 border px-3 text-xs font-semibold transition-colors ${form.target_agents.includes(agent.value) ? 'border-brand bg-brand-soft text-brand' : 'border-line-strong bg-card text-fg-soft hover:bg-muted'}`}><input type="checkbox" checked={form.target_agents.includes(agent.value)} onChange={() => toggleAgent(agent.value)} className="h-4 w-4 accent-brand" />{agent.label}</label>)}</div></fieldset>
      <div><div className="mb-2 flex items-center justify-between gap-4"><label htmlFor="skill-instructions" className="text-xs font-semibold text-fg-soft">Instructions</label><span className="text-xs tabular-nums text-fg-muted">{form.instructions.length} / 24000</span></div><textarea id="skill-instructions" value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} className={`${inputCls} min-h-64 resize-y leading-6`} maxLength={24000} placeholder={'- 每页不超过 5 个要点\n- 封面简洁\n- 结论必须有 3 条'} /><p className="mt-1.5 text-xs text-fg-muted">执行时单个 Skill 最多取 6000 字，总 Skill 上下文最多 12000 字；按优先级确定性截断。</p></div>
      <label className="flex min-h-10 items-center gap-3 text-xs text-fg-soft"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} className="h-4 w-4 accent-brand" />创建后立即启用</label>
      {error && <p role="alert" className="bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
      <div className="flex justify-end gap-2 border-t border-line pt-4"><button type="button" onClick={onClose} className="min-h-10 border border-line-strong px-4 text-xs font-semibold text-fg-soft hover:bg-muted">取消</button><button type="button" onClick={() => void save()} disabled={saving} className="inline-flex min-h-10 items-center gap-2 bg-brand px-4 text-xs font-semibold text-white hover:bg-brand-hover disabled:cursor-wait disabled:opacity-50">{saving ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Check aria-hidden="true" className="h-4 w-4" />}保存 Skill</button></div>
    </section>
  );
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<{ open: true; skill: SkillItem | null } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => { setLoading(true); setError(''); try { setSkills(await listSkills()); } catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取 Skill'); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);

  const toggle = async (skill: SkillItem) => {
    setError('');
    try { await updateSkill(skill.id, { name: skill.name, description: skill.description, instructions: skill.instructions, target_agents: skill.target_agents, priority: skill.priority, enabled: !skill.enabled }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '切换失败'); }
  };
  const remove = async (skill: SkillItem) => {
    if (!window.confirm(`删除“${skill.name}”？此操作不可撤销。`)) return;
    try { await deleteSkill(skill.id); setNotice(`已删除“${skill.name}”`); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '删除失败'); }
  };
  const handleImport = async (file?: File) => {
    if (!file) return;
    setError(''); setNotice('');
    try { const imported = await importSkill(file); setNotice(`已导入并启用“${imported.name}”`); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '导入失败'); }
    finally { if (importRef.current) importRef.current.value = ''; }
  };

  return (
    <div className="flex h-full min-w-0 flex-col bg-bg">
      <header className="page-header flex flex-shrink-0 items-end justify-between gap-6"><div><h1 className="text-fg">Skills</h1><p className="mt-1 text-sm text-fg-soft">让 Word、Excel、PPT 与 Chat Agent 稳定复用你的工作偏好</p></div><div className="flex gap-2"><input ref={importRef} type="file" accept=".md,text/markdown" onChange={(event) => void handleImport(event.target.files?.[0])} className="sr-only" aria-label="导入 Markdown Skill" /><button type="button" onClick={() => importRef.current?.click()} className="inline-flex min-h-10 items-center gap-2 border border-line-strong bg-card px-4 text-xs font-semibold text-fg-soft hover:bg-muted"><FileUp aria-hidden="true" className="h-4 w-4" />导入 .md</button><button type="button" onClick={() => setEditor({ open: true, skill: null })} className="inline-flex min-h-10 items-center gap-2 bg-sidebar px-4 text-xs font-semibold text-sidebar-strong hover:opacity-90"><Plus aria-hidden="true" className="h-4 w-4" />创建 Skill</button></div></header>
      <main className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-5xl space-y-5">
          <div className="border border-line bg-card px-4 py-3 text-xs leading-5 text-fg-soft"><strong className="text-fg">执行顺序：</strong>系统安全策略 → Agent 核心指令 → 已启用 Skill → 用户请求 → 文件内容。Skill 不会获得 API Key，也不能授权代码执行。</div>
          {error && <p role="alert" className="border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}
          {notice && <p role="status" className="border border-ok/30 bg-ok-soft px-4 py-3 text-sm text-ok">{notice}</p>}
          {loading ? <p role="status" className="settings-section flex items-center gap-2 text-sm text-fg-soft"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />正在加载 Skills…</p> : skills.length === 0 ? (
            <section className="flex min-h-64 flex-col items-center justify-center border border-line bg-card px-6 text-center"><BookOpen aria-hidden="true" className="mb-4 h-10 w-10 text-fg-muted" /><h2 className="text-base font-semibold text-fg">还没有 Skill</h2><p className="mt-2 max-w-lg text-sm leading-6 text-fg-soft">创建一组可复用指令，或导入带 OfficeAgent frontmatter 的 Markdown 文件。</p></section>
          ) : (
            <section className="border border-line bg-card" aria-label="Skill 列表"><div className="grid grid-cols-[minmax(0,1fr)_120px_90px_112px] border-b border-line px-4 py-2 text-[10px] font-semibold tracking-wide text-fg-muted"><span>SKILL</span><span>AGENTS</span><span>PRIORITY</span><span className="text-right">操作</span></div>{skills.map((skill) => <article key={skill.id} className="grid grid-cols-[minmax(0,1fr)_120px_90px_112px] items-center gap-3 border-b border-line px-4 py-3 last:border-b-0"><div className="min-w-0"><div className="flex items-center gap-2"><h2 className="truncate text-sm font-semibold text-fg" title={skill.name}>{skill.name}</h2><span className={`text-[10px] font-semibold ${skill.enabled ? 'text-ok' : 'text-fg-muted'}`}>{skill.enabled ? '已启用' : '已停用'}</span></div><p className="mt-1 truncate text-xs text-fg-muted" title={skill.description}>{skill.description || '无描述'} · {skill.source === 'import' ? 'Markdown 导入' : 'UI 创建'}</p></div><span className="truncate text-xs text-fg-soft" title={skill.target_agents.join(', ')}>{skill.target_agents.join(' · ')}</span><span className="font-mono text-xs tabular-nums text-fg-soft">{skill.priority}</span><div className="flex justify-end gap-1"><button type="button" onClick={() => void toggle(skill)} aria-label={`${skill.enabled ? '停用' : '启用'} ${skill.name}`} className={`inline-grid min-h-9 min-w-9 place-items-center ${skill.enabled ? 'text-ok hover:bg-ok-soft' : 'text-fg-muted hover:bg-muted'}`}><Power aria-hidden="true" className="h-4 w-4" /></button><button type="button" onClick={() => setEditor({ open: true, skill })} aria-label={`编辑 ${skill.name}`} className="inline-grid min-h-9 min-w-9 place-items-center text-fg-muted hover:bg-muted hover:text-brand"><Pencil aria-hidden="true" className="h-4 w-4" /></button><button type="button" onClick={() => void remove(skill)} aria-label={`删除 ${skill.name}`} className="inline-grid min-h-9 min-w-9 place-items-center text-fg-muted hover:bg-danger-soft hover:text-danger"><Trash2 aria-hidden="true" className="h-4 w-4" /></button></div></article>)}</section>
          )}
          {editor && <SkillEditor key={editor.skill?.id || 'new'} current={editor.skill} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setNotice('Skill 已保存；下一次 Agent 任务会自动解析已启用 Skill。'); void load(); }} />}
        </div>
      </main>
    </div>
  );
}
