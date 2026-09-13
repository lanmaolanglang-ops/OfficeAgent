import { useEffect } from 'react';
import { Clock, RefreshCw, FileOutput, ChevronLeft, ChevronRight } from 'lucide-react';
import { useTaskStore } from '../../stores';
import TaskStatusBadge from '../../components/Tasks/TaskStatusBadge';
import { getFileUrl } from '../../services/api';
import type { Task } from '../../types';

/** 列表行的稳定 key：优先实体 id，缺失时用"类型+创建时间"而不是数组下标。 */
function taskRowKey(task: Task, index: number): string {
  return task.id || `${task.type ?? 'unknown'}:${task.created_at ?? ''}:${index}`;
}

/** 输出文件条目的稳定 key：优先 file_id，缺失时用文件名而不是数组下标。 */
function outputFileKey(file: { file_id?: string; filename?: string }, index: number): string {
  return file.file_id || `${file.filename ?? 'file'}:${index}`;
}

/** 非法/缺失时间显式兜底，避免渲染出 "Invalid Date"（M37②）。 */
function formatDateTime(value?: string): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString('zh-CN');
}

export default function TaskHistory() {
  const { tasks, total, page, pageSize, loadTasks, loading, loadError } = useTaskStore();

  useEffect(() => { void loadTasks(1); }, [loadTasks]);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = page > 1;
  const canNext = page < pageCount;

  return (
    <div className="flex flex-col h-full bg-bg">
      <div className="page-header flex flex-shrink-0 items-center justify-between">
        <div>
          <h1 className="text-fg">任务历史</h1>
          <p className="text-sm text-fg-soft mt-1">查看所有任务记录</p>
        </div>
        <button
          onClick={() => void loadTasks()}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 text-sm text-fg-muted hover:text-fg hover:bg-muted rounded-xl transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {loadError && (
        <div role="alert" className="mx-8 mb-2 flex-shrink-0 bg-danger-soft px-4 py-2 text-sm text-danger">
          无法连接后端服务，当前显示的可能不是最新任务列表。请确认后端已启动后点击刷新。
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-fg-muted">
            <Clock className="w-12 h-12 mb-3" />
            <p className="text-sm">暂无任务记录</p>
          </div>
        ) : (
          <div className="max-w-3xl space-y-2">
            {tasks.map((task, index) => (
              <div key={taskRowKey(task, index)} className="border border-line bg-card p-4 transition-colors hover:border-line-strong">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="text-sm font-medium text-fg">{task.type?.replace(/_/g, ' ')}</span>
                      <TaskStatusBadge status={task.status} />
                      <span className="text-xs text-fg-muted">{formatDateTime(task.created_at)}</span>
                    </div>
                    {task.current_step && (
                      <p className="text-xs text-fg-soft">{task.current_step}</p>
                    )}
                    {task.error && (
                      <p role="alert" className="mt-2 bg-danger-soft p-2 text-xs text-danger">{task.error}</p>
                    )}
                    {task.output_files && task.output_files.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {task.output_files.map((file, i) => (
                          <a key={outputFileKey(file, i)} href={getFileUrl(file.file_id)} target="_blank" rel="noopener" download={file.filename} className="flex items-center gap-1 text-xs bg-brand-soft text-brand px-2 py-1 rounded-lg hover:bg-brand hover:text-white">
                            <FileOutput className="w-3 h-3" />
                            {file.filename}
                          </a>
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

      {/* P5-6：修复前只取前 50 条且没有分页控件，更早的历史不可见 */}
      <div className="flex flex-shrink-0 items-center justify-between border-t border-line px-8 py-3 text-xs text-fg-muted">
        <span>共 {total} 条 · 第 {page} / {pageCount} 页</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void loadTasks(page - 1)}
            disabled={!canPrev || loading}
            aria-label="上一页"
            className="flex items-center gap-1 rounded-lg px-2 py-1 hover:bg-muted disabled:opacity-40"
          >
            <ChevronLeft className="w-4 h-4" />上一页
          </button>
          <button
            type="button"
            onClick={() => void loadTasks(page + 1)}
            disabled={!canNext || loading}
            aria-label="下一页"
            className="flex items-center gap-1 rounded-lg px-2 py-1 hover:bg-muted disabled:opacity-40"
          >
            下一页<ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
