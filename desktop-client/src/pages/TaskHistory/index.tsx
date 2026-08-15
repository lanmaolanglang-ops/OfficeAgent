import { useEffect } from 'react';
import {
  CheckCircle, AlertCircle, Loader2, Clock, XCircle, RefreshCw, FileOutput,
} from 'lucide-react';
import { useTaskStore } from '../../stores';
import type { TaskStatus } from '../../types';

function StatusBadge({ status }: { status: TaskStatus }) {
  const config: Record<TaskStatus, { label: string; class: string; icon: typeof Clock }> = {
    pending: { label: '等待中', class: 'bg-yellow-500/10 text-yellow-400', icon: Clock },
    processing: { label: '处理中', class: 'bg-indigo-500/10 text-indigo-400', icon: Loader2 },
    completed: { label: '已完成', class: 'bg-green-500/10 text-green-400', icon: CheckCircle },
    failed: { label: '失败', class: 'bg-red-500/10 text-red-400', icon: AlertCircle },
    cancelled: { label: '已取消', class: 'bg-gray-500/10 text-gray-400', icon: XCircle },
  };
  const { label, class: cls, icon: Icon } = config[status];
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      <Icon className={`w-3 h-3 ${status === 'processing' ? 'animate-spin' : ''}`} />
      {label}
    </span>
  );
}

export default function TaskHistory() {
  const { tasks, loadTasks, loading } = useTaskStore();

  useEffect(() => { loadTasks(); }, [loadTasks]);

  return (
    <div className="flex flex-col h-full bg-[#111111]">
      <div className="px-8 py-6 flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-2xl font-semibold text-white tracking-tight">任务历史</h1>
          <p className="text-sm text-gray-500 mt-1">查看所有任务记录</p>
        </div>
        <button
          onClick={loadTasks}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 text-sm text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded-xl transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-600">
            <Clock className="w-12 h-12 mb-3" />
            <p className="text-sm">暂无任务记录</p>
          </div>
        ) : (
          <div className="max-w-3xl space-y-2">
            {tasks.map((task) => (
              <div key={task.id} className="bg-[#181818] rounded-xl border border-[#2A2A2A] p-4 hover:border-gray-700 transition-colors">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="text-sm font-medium text-gray-200">{task.type?.replace(/_/g, ' ')}</span>
                      <StatusBadge status={task.status} />
                      <span className="text-xs text-gray-600">{new Date(task.created_at).toLocaleString('zh-CN')}</span>
                    </div>
                    {task.current_step && (
                      <p className="text-xs text-gray-500">{task.current_step}</p>
                    )}
                    {task.error && (
                      <p className="text-xs text-red-400 mt-2 bg-red-500/10 p-2 rounded-lg">{task.error}</p>
                    )}
                    {task.result?.files && task.result.files.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {task.result.files.map((file, i) => (
                          <div key={i} className="flex items-center gap-1 text-xs bg-indigo-500/10 text-indigo-300 px-2 py-1 rounded-lg">
                            <FileOutput className="w-3 h-3" />
                            {file.name}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
