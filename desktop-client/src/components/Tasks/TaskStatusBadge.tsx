import { CheckCircle, AlertCircle, Loader2, Clock, XCircle } from 'lucide-react';
import type { TaskStatus } from '../../types';

const STATUS_CONFIG: Record<TaskStatus, { label: string; class: string; icon: typeof Clock }> = {
  pending: { label: '等待中', class: 'bg-muted text-fg-soft', icon: Clock },
  processing: { label: '处理中', class: 'bg-brand-soft text-brand', icon: Loader2 },
  completed: { label: '已完成', class: 'bg-ok-soft text-ok', icon: CheckCircle },
  failed: { label: '失败', class: 'bg-danger-soft text-danger', icon: AlertCircle },
  cancelled: { label: '已取消', class: 'bg-muted text-fg-muted', icon: XCircle },
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
