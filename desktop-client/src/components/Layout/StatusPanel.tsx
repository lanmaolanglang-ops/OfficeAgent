import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Presentation, Sheet, File, Loader2, Clock3, Server, Cpu, HardDrive, Database, Box, ArrowUpRight } from 'lucide-react';
import { useBackendStore, useTaskStore, useFileStore } from '../../stores';

function getFileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'docx') return { icon: FileText, cls: 'file-icon-word' };
  if (ext === 'pptx') return { icon: Presentation, cls: 'file-icon-ppt' };
  if (ext === 'xlsx') return { icon: Sheet, cls: 'file-icon-excel' };
  return { icon: File, cls: 'file-icon-generic' };
}
function formatSize(bytes: number) { if (!bytes) return ''; return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function checkOk(status?: string) { return ['ok', 'healthy', 'connected', 'running', 'configured'].includes(status || ''); }

export default function StatusPanel() {
  const navigate = useNavigate();
  const { connected, degraded, health, check, startPolling, stopPolling } = useBackendStore();
  const { tasks, startPolling: startTaskPolling, stopPolling: stopTaskPolling } = useTaskStore();
  const { files, loadFiles } = useFileStore();

  useEffect(() => {
    startPolling(); startTaskPolling(); loadFiles();
    return () => { stopPolling(); stopTaskPolling(); };
  }, [startPolling, stopPolling, startTaskPolling, stopTaskPolling, loadFiles]);

  const currentTask = tasks.find(t => t.status === 'processing' || t.status === 'pending');
  const recentFiles = files.slice(0, 5);
  const checks = health?.checks || {};
  const modelCheck = checks.models as { status?: string; model?: string; provider?: string } | undefined;
  const modelStatus = modelCheck?.status;
  const modelLabel = !connected ? '未检测' : modelStatus === 'configured' ? (modelCheck?.provider || modelCheck?.model || '已配置') : modelStatus === 'template' ? '模板模式' : modelStatus === 'error' ? '异常' : '检测中';

  const statusItems = [
    { label: 'API 服务', value: connected ? '正常' : degraded ? '降级' : '未连接', icon: Server, ok: connected },
    { label: '数据库', value: !connected ? '未检测' : checkOk(checks.database?.status) ? '正常' : '检测中', icon: Database, ok: connected && checkOk(checks.database?.status) },
    { label: 'AI 模型', value: modelLabel, icon: Cpu, ok: connected && modelStatus === 'configured' },
    { label: '文件存储', value: !connected ? '未检测' : checkOk(checks.storage?.status) ? '正常' : '检测中', icon: HardDrive, ok: connected && checkOk(checks.storage?.status) },
    { label: '任务引擎', value: !connected ? '不可用' : checks.workers?.active_tasks ? `${checks.workers.active_tasks} 项运行中` : '空闲', icon: Box, ok: connected && checkOk(checks.workers?.status) },
  ];

  return (
    <aside className="status-panel flex w-[304px] flex-shrink-0 flex-col overflow-hidden border-l border-line">
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <section className="side-card">
          <div className="side-card-title"><span>任务中心</span><button onClick={() => navigate('/history')}>查看全部</button></div>
          {currentTask ? (
            <div className="task-focus">
              <div className="flex items-start gap-3"><div className="task-focus-icon"><Loader2 className="w-4 h-4 animate-spin" /></div><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-fg truncate">{currentTask.type?.replace(/_/g, ' ') || '任务处理中'}</p><p className="text-xs text-fg-muted mt-1 truncate">{currentTask.current_step || '正在执行任务'}</p></div><span className="text-xs font-semibold text-brand">{currentTask.progress || 0}%</span></div>
              <div className="h-1.5 bg-muted rounded-full mt-3 overflow-hidden"><div className="h-full bg-brand rounded-full transition-all" style={{ width: `${currentTask.progress || 0}%` }} /></div>
            </div>
          ) : (
            <div className="empty-state"><Clock3 className="w-5 h-5" /><div><p>当前没有执行中的任务</p><span>新任务会在这里显示进度</span></div></div>
          )}
        </section>

        <section className="side-card">
          <div className="side-card-title"><span>Agent 状态</span><button type="button" onClick={() => void check()}>{connected ? '重新检测' : '重试连接'}</button></div>
          <div className="divide-y divide-line">
            {statusItems.map(item => <div key={item.label} className="status-row"><item.icon className="w-4 h-4 text-fg-muted" /><span className="flex-1 text-xs text-fg-muted">{item.label}</span><span className={`status-dot ${item.ok ? 'status-dot-ok' : 'status-dot-off'}`} /><span className={`text-xs ${item.ok ? 'text-ok' : 'text-fg-muted'}`}>{item.value}</span></div>)}
          </div>
        </section>

        <section className="side-card">
          <div className="side-card-title"><span>最近文件</span><button onClick={() => navigate('/files')}>查看全部</button></div>
          {recentFiles.length === 0 ? <div className="empty-state"><File className="w-5 h-5" /><div><p>还没有文件</p><span>上传后的文件会显示在这里</span></div></div> : (
            <div className="space-y-1">
              {recentFiles.map(file => { const fi = getFileIcon(file.name || ''); const Icon = fi.icon; return <button key={file.id} onClick={() => navigate('/files')} className="recent-file"><span className={`recent-file-icon ${fi.cls}`}><Icon className="w-4 h-4" /></span><span className="min-w-0 flex-1 text-left"><span className="block text-xs text-fg-soft truncate">{file.name}</span><span className="block text-xs text-fg-faint mt-0.5">{formatSize(file.size)}</span></span><ArrowUpRight className="w-3.5 h-3.5 text-fg-faint" /></button>; })}
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}
