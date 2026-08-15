import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Presentation, Sheet, File, Loader2, Clock3, Server, Cpu, HardDrive, Database, Box, ArrowUpRight } from 'lucide-react';
import { useBackendStore, useTaskStore, useFileStore } from '../../stores';

function getFileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'docx' || ext === 'doc') return { icon: FileText, cls: 'file-icon-word' };
  if (ext === 'pptx' || ext === 'ppt') return { icon: Presentation, cls: 'file-icon-ppt' };
  if (ext === 'xlsx' || ext === 'xls') return { icon: Sheet, cls: 'file-icon-excel' };
  return { icon: File, cls: 'file-icon-generic' };
}
function formatSize(bytes: number) { if (!bytes) return ''; return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function checkOk(status?: string) { return ['ok', 'healthy', 'connected', 'running', 'configured'].includes(status || ''); }

export default function StatusPanel() {
  const navigate = useNavigate();
  const { connected, degraded, health, startPolling, stopPolling } = useBackendStore();
  const { tasks, loadTasks } = useTaskStore();
  const { files, loadFiles } = useFileStore();

  useEffect(() => {
    startPolling(); loadTasks(); loadFiles();
    const taskInterval = setInterval(() => { loadTasks(); }, 3000);
    return () => { stopPolling(); clearInterval(taskInterval); };
  }, [startPolling, stopPolling, loadTasks, loadFiles]);

  const currentTask = tasks.find(t => t.status === 'processing' || t.status === 'pending');
  const recentFiles = files.slice(0, 5);
  const checks = health?.checks || {};
  const modelCheck = checks.models as { status?: string; model?: string; provider?: string } | undefined;
  const modelStatus = modelCheck?.status;
  const modelLabel = modelStatus === 'configured' ? (modelCheck?.provider || modelCheck?.model || '已配置') : modelStatus === 'template' ? '模板模式' : modelStatus === 'error' ? '异常' : '检测中';

  const statusItems = [
    { label: 'API 服务', value: connected ? '正常' : degraded ? '降级' : '未连接', icon: Server, ok: connected },
    { label: '数据库', value: checkOk(checks.database?.status) ? '正常' : '检测中', icon: Database, ok: checkOk(checks.database?.status) },
    { label: 'AI 模型', value: modelLabel, icon: Cpu, ok: modelStatus === 'configured' },
    { label: '文件存储', value: checkOk(checks.storage?.status) ? '正常' : '检测中', icon: HardDrive, ok: checkOk(checks.storage?.status) },
    { label: '任务引擎', value: checks.workers?.active_tasks ? `${checks.workers.active_tasks} 项运行中` : '空闲', icon: Box, ok: checkOk(checks.workers?.status) },
  ];

  return (
    <aside className="status-panel w-[304px] bg-[#f8f9fc] border-l border-[#e8ebf2] flex flex-col flex-shrink-0 overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <section className="side-card">
          <div className="side-card-title"><span>任务中心</span><button onClick={() => navigate('/history')}>查看全部</button></div>
          {currentTask ? (
            <div className="task-focus">
              <div className="flex items-start gap-3"><div className="task-focus-icon"><Loader2 className="w-4 h-4 animate-spin" /></div><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-[#34405a] truncate">{currentTask.type?.replace(/_/g, ' ') || '任务处理中'}</p><p className="text-[10px] text-[#929bad] mt-1 truncate">{currentTask.current_step || '正在执行任务'}</p></div><span className="text-[10px] font-semibold text-[#5578eb]">{currentTask.progress || 0}%</span></div>
              <div className="h-1.5 bg-[#e8ebf2] rounded-full mt-3 overflow-hidden"><div className="h-full bg-[#587cf0] rounded-full transition-all" style={{ width: `${currentTask.progress || 0}%` }} /></div>
            </div>
          ) : (
            <div className="empty-state"><Clock3 className="w-5 h-5" /><div><p>当前没有执行中的任务</p><span>新任务会在这里显示进度</span></div></div>
          )}
        </section>

        <section className="side-card">
          <div className="side-card-title"><span>Agent 状态</span><span className="normal-case font-normal text-[#a1a9b8]">实时</span></div>
          <div className="divide-y divide-[#edf0f5]">
            {statusItems.map(item => <div key={item.label} className="status-row"><item.icon className="w-4 h-4 text-[#8993a7]" /><span className="flex-1 text-xs text-[#667188]">{item.label}</span><span className={`status-dot ${item.ok ? 'status-dot-ok' : 'status-dot-off'}`} /><span className={`text-[10px] ${item.ok ? 'text-[#22a96f]' : 'text-[#9aa3b3]'}`}>{item.value}</span></div>)}
          </div>
        </section>

        <section className="side-card">
          <div className="side-card-title"><span>最近文件</span><button onClick={() => navigate('/files')}>查看全部</button></div>
          {recentFiles.length === 0 ? <div className="empty-state"><File className="w-5 h-5" /><div><p>还没有文件</p><span>上传后的文件会显示在这里</span></div></div> : (
            <div className="space-y-1">
              {recentFiles.map(file => { const fi = getFileIcon(file.name || ''); const Icon = fi.icon; return <button key={file.id} onClick={() => navigate('/files')} className="recent-file"><span className={`recent-file-icon ${fi.cls}`}><Icon className="w-4 h-4" /></span><span className="min-w-0 flex-1 text-left"><span className="block text-xs text-[#46516a] truncate">{file.name}</span><span className="block text-[10px] text-[#a1a9b7] mt-0.5">{formatSize(file.size)}</span></span><ArrowUpRight className="w-3.5 h-3.5 text-[#b2b9c6]" /></button>; })}
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}
