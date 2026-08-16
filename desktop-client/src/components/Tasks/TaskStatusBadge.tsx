import { CheckCircle, AlertCircle, Loader2, Clock, XCircle } from 'lucide-react';
import type { TaskStatus } from '../../types';

const STATUS_CONFIG: Record<TaskStatus, { label: string; class: string; icon: typeof Clock }> = {
  pending: { label: '等待中', class: 'bg-yellow-500/10 text-yellow-400', icon: Clock },
  processing: { label: '处理中', class: 'bg-indigo-500/10 text-indigo-400', icon: Loader2 },
  completed: { label: '已完成', class: 'bg-green-500/10 text-green-400', icon: CheckCircle },
  failed: { label: '失败', class: 'bg-red-500/10 text-red-400', icon: AlertCircle },
  cancelled: { label: '已取消', class: 'bg-gray-500/10 text-gray-400', icon: XCircle },
};

/**
 * 任务状态徽标，供任务历史、状态面板等处复用。
 */
export default function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const { label, class: cls, icon: Icon } = STATUS_CONFIG[status];
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      <Icon className={`w-3 h-3 ${status === 'processing' ? 'animate-spin' : ''}`} />
      {label}
    </span>
  );
}
