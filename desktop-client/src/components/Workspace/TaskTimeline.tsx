import { Check, Loader2, Circle } from 'lucide-react';

export interface TimelineStep {
  label: string;
  status: 'done' | 'running' | 'pending';
  description?: string;
}

interface TaskTimelineProps {
  steps: TimelineStep[];
  title?: string;
}

export default function TaskTimeline({ steps, title = '任务执行' }: TaskTimelineProps) {
  if (steps.length === 0) return null;

  return (
    <div className="w-full rounded-2xl bg-card border border-line p-5 shadow-[var(--shadow-panel)]">
      <h3 className="text-sm font-semibold text-fg mb-4">{title}</h3>
      <div className="space-y-0">
        {steps.map((step, index) => (
          <div key={index} className="flex gap-3">
            {/* Timeline line + dot */}
            <div className="flex flex-col items-center">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${
                step.status === 'done' ? 'bg-ok-soft' :
                step.status === 'running' ? 'bg-brand-soft' : 'bg-muted'
              }`}>
                {step.status === 'done' ? (
                  <Check className="w-3.5 h-3.5 text-ok" />
                ) : step.status === 'running' ? (
                  <Loader2 className="w-3.5 h-3.5 text-brand animate-spin" />
                ) : (
                  <Circle className="w-2.5 h-2.5 text-fg-faint" />
                )}
              </div>
              {index < steps.length - 1 && (
                <div className={`w-0.5 flex-1 min-h-[24px] my-1 ${
                  step.status === 'done' ? 'bg-brand-soft' : 'bg-line'
                }`} />
              )}
            </div>
            {/* Content */}
            <div className={`pb-5 ${index === steps.length - 1 ? 'pb-0' : ''}`}>
              <p className={`text-sm ${
                step.status === 'done' ? 'text-fg-soft' :
                step.status === 'running' ? 'text-fg font-medium' : 'text-fg-muted'
              }`}>
                {step.label}
              </p>
              {step.description && (
                <p className="text-xs text-fg-muted mt-0.5">{step.description}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
