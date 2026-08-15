import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Bot, FileText, Presentation, Sheet, Sparkles, User } from 'lucide-react';
import FileUploader from './FileUploader';
import TaskTimeline, { type TimelineStep } from './TaskTimeline';
import ChatInput from './ChatInput';
import { useChatStore, useTaskStore } from '../../stores';
import type { Task, AgentType } from '../../types';
import heroAsset from '../../assets/hero.png';

interface WorkspaceProps { title?: string; subtitle?: string; agent?: AgentType; }

function taskToSteps(task: Task | undefined): TimelineStep[] {
  if (!task) return [];
  const steps: TimelineStep[] = [{ label: '文件读取完成', status: 'done' }, { label: '任务内容分析', status: 'done' }];
  if (task.status === 'processing') steps.push({ label: task.current_step || '正在处理...', status: 'running' }, { label: '输出结果文件', status: 'pending' });
  else if (task.status === 'completed') steps.push({ label: '处理完成', status: 'done' }, { label: '输出结果文件', status: 'done' });
  else if (task.status === 'failed') steps.push({ label: '处理失败', status: 'pending', description: task.error });
  else steps.push({ label: '等待处理', status: 'pending' }, { label: '输出结果文件', status: 'pending' });
  return steps;
}

const agents = [
  { path: '/word', title: 'Word Agent', description: '文档整理、格式优化与内容处理', icon: FileText, tone: 'agent-word' },
  { path: '/ppt', title: 'PPT Agent', description: '主题生成、内容规划与演示设计', icon: Presentation, tone: 'agent-ppt' },
  { path: '/excel', title: 'Excel Agent', description: '数据分析、汇总与图表生成', icon: Sheet, tone: 'agent-excel' },
];

export default function Workspace({ title = '工作台', subtitle = '智能办公助手', agent = 'auto' }: WorkspaceProps) {
  const navigate = useNavigate();
  const { messages, sending, sendMessage, setAgent } = useChatStore();
  const { tasks, loadTasks } = useTaskStore();
  const [localSteps, setLocalSteps] = useState<TimelineStep[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { const interval = setInterval(() => { loadTasks(); }, 2000); return () => clearInterval(interval); }, [loadTasks]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  // 页面切换时同步当前 Agent 模式（Dashboard 为 auto）
  useEffect(() => { setAgent(agent); }, [agent, setAgent]);

  const currentTask = tasks.find(t => t.status === 'processing' || t.status === 'pending');
  const displaySteps = currentTask ? taskToSteps(currentTask) : localSteps;
  const isDashboard = agent === 'auto';

  const handleSend = async (message: string) => {
    setLocalSteps([{ label: '理解任务需求', status: 'running' }, { label: '选择合适的 Agent', status: 'pending' }, { label: '执行处理', status: 'pending' }, { label: '输出结果', status: 'pending' }]);
    await sendMessage(message);
    setLocalSteps([]);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#f4f6fa] min-w-0">
      <div className="flex-1 overflow-y-auto px-5 pt-5 pb-3">
        <div className="max-w-[980px] mx-auto space-y-4">
          {messages.length === 0 && !currentTask && (
            <>
              <section className="welcome-banner">
                <div className="relative z-10 max-w-[610px]">
                  <span className="eyebrow"><Sparkles className="w-3.5 h-3.5" /> Office AI Workspace</span>
                  <h1 className="mt-3 text-[25px] font-bold text-[#172036] leading-tight">{isDashboard ? '今天想处理什么办公任务？' : title}</h1>
                  <p className="mt-2 text-sm text-[#66718a]">{isDashboard ? '上传文件并描述目标，AI 会自动选择合适的处理方式。' : subtitle}</p>
                </div>
                <img src={heroAsset} className="hero-asset" alt="" />
              </section>

              {isDashboard && (
                <section className="agent-grid">
                  {agents.map((agent) => {
                    const Icon = agent.icon;
                    return (
                      <button key={agent.path} onClick={() => navigate(agent.path)} className="agent-tile">
                        <div className={`agent-icon ${agent.tone}`}><Icon className="w-5 h-5" /></div>
                        <div className="min-w-0 text-left">
                          <p className="text-sm font-semibold text-[#25304a]">{agent.title}</p>
                          <p className="text-[11px] text-[#8a94a8] mt-1 leading-relaxed">{agent.description}</p>
                        </div>
                        <ArrowRight className="w-4 h-4 text-[#9ca6b8] ml-auto flex-shrink-0" />
                      </button>
                    );
                  })}
                </section>
              )}

              <section className="surface-panel p-4">
                <div className="flex items-center justify-between mb-3">
                  <div><h2 className="text-sm font-semibold text-[#27324a]">添加任务文件</h2><p className="text-[11px] text-[#98a1b3] mt-0.5">文件上传成功后将自动附加到下一条任务</p></div>
                </div>
                <FileUploader />
              </section>
            </>
          )}

          {messages.length > 0 && (
            <section className="surface-panel p-5 min-h-[420px]">
              <div className="flex items-center justify-between pb-4 mb-5 border-b border-[#edf0f5]">
                <div><h1 className="text-base font-semibold text-[#25304a]">智能对话</h1><p className="text-[11px] text-[#98a1b3] mt-1">{subtitle}</p></div>
              </div>
              <div className="space-y-5">
                {messages.map(msg => (
                  <div key={msg.id} className={`flex gap-3 animate-fade-in ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                    <div className={`message-avatar ${msg.role === 'user' ? 'message-avatar-user' : 'message-avatar-ai'}`}>{msg.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}</div>
                    <div className={`max-w-[78%] rounded-lg px-4 py-3 text-sm leading-relaxed ${msg.role === 'user' ? 'bg-[#edf3ff] text-[#30446d]' : 'bg-[#f7f8fb] text-[#4a556d] border border-[#edf0f5]'}`}>
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                      {msg.role === 'assistant' && msg.task_status === 'processing' && <div className="mt-3 h-1.5 bg-[#e7eaf0] rounded-full overflow-hidden"><div className="h-full bg-[#557cf3] rounded-full transition-all duration-500" style={{ width: `${msg.task_progress || 0}%` }} /></div>}
                    </div>
                  </div>
                ))}
                {sending && !messages.some(m => m.task_status === 'processing') && <div className="flex gap-3"><div className="message-avatar message-avatar-ai"><Bot className="w-4 h-4" /></div><div className="bg-[#f7f8fb] border border-[#edf0f5] rounded-lg px-4 py-3 flex gap-1"><span className="typing-dot" /><span className="typing-dot [animation-delay:150ms]" /><span className="typing-dot [animation-delay:300ms]" /></div></div>}
                <div ref={messagesEndRef} />
              </div>
            </section>
          )}

          {displaySteps.length > 0 && <TaskTimeline steps={displaySteps} title={currentTask ? '任务执行中' : '正在处理'} />}
        </div>
      </div>

      <div className="px-5 pb-4 pt-2 flex-shrink-0 bg-[#f4f6fa]">
        <div className="max-w-[980px] mx-auto"><ChatInput onSend={handleSend} disabled={sending} /></div>
      </div>
    </div>
  );
}
