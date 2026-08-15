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
    <div className="w-full rounded-2xl bg-[#181818] border border-[#2A2A2A] p-5">
      <h3 className="text-sm font-medium text-gray-200 mb-4">{title}</h3>
      <div className="space-y-0">
        {steps.map((step, index) => (
          <div key={index} className="flex gap-3">
            {/* Timeline line + dot */}
            <div className="flex flex-col items-center">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${
                step.status === 'done' ? 'bg-green-500/20' :
                step.status === 'running' ? 'bg-indigo-500/20' : 'bg-[#222222]'
              }`}>
                {step.status === 'done' ? (
                  <Check className="w-3.5 h-3.5 text-green-400" />
                ) : step.status === 'running' ? (
                  <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin" />
                ) : (
                  <Circle className="w-2.5 h-2.5 text-gray-600" />
                )}
              </div>
              {index < steps.length - 1 && (
                <div className={`w-0.5 flex-1 min-h-[24px] my-1 ${
                  step.status === 'done' ? 'bg-green-500/30' : 'bg-[#2A2A2A]'
                }`} />
              )}
            </div>
            {/* Content */}
            <div className={`pb-5 ${index === steps.length - 1 ? 'pb-0' : ''}`}>
              <p className={`text-sm ${
                step.status === 'done' ? 'text-gray-400' :
                step.status === 'running' ? 'text-white font-medium' : 'text-gray-600'
              }`}>
                {step.label}
              </p>
              {step.description && (
                <p className="text-xs text-gray-600 mt-0.5">{step.description}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
