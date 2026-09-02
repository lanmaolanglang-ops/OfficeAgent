import { create } from 'zustand';
import type { ChatMessage, AgentType, ChatResponse } from '../types';
import { sendChatMessage, getTask, getFileUrl } from '../services/api';
import { sendNotification } from '../services/tauri';
import { useFileStore } from './fileStore';
import { useSettingsStore } from './settingsStore';

interface ChatState {
  messages: ChatMessage[];
  sending: boolean;
  currentAgent: AgentType;
  conversationId?: string;
  lastOutputFileId?: string;
  sendMessage: (content: string) => Promise<void>;
  setAgent: (agent: AgentType) => void;
  clearChat: () => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
}

function generateId(): string {
  return Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
}

// 轮询代际令牌：新消息/清空会话时自增，使旧轮询链失效（定时器仍触发但立即空转）
let pollGeneration = 0;

// 触发浏览器/WebView 下载，用于「完成后自动打开结果」
function triggerDownload(url: string, filename?: string) {
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  // download 对跨源 URL 可能被浏览器忽略；新窗口可避免 WebView 离开应用。
  a.target = '_blank';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ============ 会话持久化：刷新后对话不丢，进行中任务可续接 ============
const CHAT_STORAGE_KEY = 'officeagent_chat_v1';
const PERSIST_MAX_MESSAGES = 50;

function persistChat(state: { messages: ChatMessage[]; conversationId?: string; lastOutputFileId?: string }) {
  try {
    // 只保留最近 50 条；task_result 可能很大，仅最后一条保留完整结果
    const messages = state.messages.slice(-PERSIST_MAX_MESSAGES).map((m, idx, arr) => (
      idx < arr.length - 1 ? { ...m, task_result: undefined } : m
    ));
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
      messages,
      conversationId: state.conversationId,
      lastOutputFileId: state.lastOutputFileId,
    }));
  } catch {
    // 存储满/不可写时静默放弃持久化（功能降级，不影响使用）
  }
}

function loadPersistedChat(): Pick<ChatState, 'messages'> & { conversationId?: string; lastOutputFileId?: string } {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return { messages: [] };
    const parsed = JSON.parse(raw) as {
      messages?: ChatMessage[];
      conversationId?: string;
      lastOutputFileId?: string;
    };
    const messages = Array.isArray(parsed.messages) ? parsed.messages : [];
    return {
      messages,
      conversationId: typeof parsed.conversationId === 'string' ? parsed.conversationId : undefined,
      lastOutputFileId: typeof parsed.lastOutputFileId === 'string' ? parsed.lastOutputFileId : undefined,
    };
  } catch {
    return { messages: [] };
  }
}

export const useChatStore = create<ChatState>((set, get) => {
  const persisted = typeof window !== 'undefined' ? loadPersistedChat() : { messages: [] as ChatMessage[] };

  return ({
    messages: persisted.messages,
    sending: false,
    currentAgent: 'auto',
    conversationId: persisted.conversationId,
    lastOutputFileId: persisted.lastOutputFileId,

    updateMessage: (id: string, updates: Partial<ChatMessage>) => {
      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === id ? { ...m, ...updates } : m
        ),
      }));
    },

    sendMessage: async (content: string) => {
      // 并发守卫：上一轮任务仍在轮询时忽略新的发送，避免 sending 状态竞态
      if (get().sending) return;
      const generation = ++pollGeneration;
      // 在插入占位消息之前快照历史，避免把“正在理解…”占位内容发给后端
      const history = get().messages
        .filter((m) => m.role === 'user' || (m.role === 'assistant' && (m.task_status === 'completed' || m.task_status === 'failed' || m.task_status === 'cancelled')))
        .slice(-10)
        .map((m) => ({
          role: m.role,
          // 剥离下载链接等噪声，避免污染模型上下文
          content: m.content.replace(/下载: \S+/g, '').replace(/\n{3,}/g, '\n\n').trim(),
        }));
      // 只发送用户当前明确附加的文件。没有附件的同会话跟进由后端根据
      // conversation_id 恢复最近产物；前端强塞 lastOutputFileId 会让跨 Agent
      // 新任务被旧文件类型错误路由。
      const fileState = useFileStore.getState();
      const templateFileId = fileState.templateFileId;
      // PPT 模板通过独立字段传递，不能同时作为普通输入文件，否则任务会把
      // 模板内容误当成待处理源文件。
      const attachedFileIds = fileState.attachedFileIds.filter((id) => id !== templateFileId);
      const userMessage: ChatMessage = {
        id: generateId(),
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
        agent: get().currentAgent,
      };

      const assistantId = generateId();
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '🤔 正在理解您的需求...',
        timestamp: new Date().toISOString(),
        task_status: 'pending',
        task_progress: 0,
      };

      set((state) => ({
        messages: [...state.messages, userMessage, assistantMessage],
        sending: true,
      }));

      let taskId: string | undefined;

      try {
        // 模型由后端“默认模型”机制决定，前端不再传 model_config
        const response: ChatResponse = await sendChatMessage({
          message: content,
          agent: get().currentAgent,
          file_ids: attachedFileIds,
          template_file_id: templateFileId || undefined,
          conversation_id: get().conversationId,
          history,
        });

        // The Backend accepted the task, so these attachments no longer belong
        // to the next message. Request failures leave the snapshot attached.
        useFileStore.getState().clearAttachments();

        taskId = response.task_id;
        if (response.conversation_id) {
          set({ conversationId: response.conversation_id });
        }
        const agentName = response.agent || '智能助手';

        get().updateMessage(assistantId, {
          content: `✅ 已为您分配 ${agentName} 处理，正在执行中...`,
          task_id: taskId,
          is_follow_up: response.is_follow_up,
          parent_task_id: response.parent_task_id,
          revision_mode: response.revision_mode,
          revision_number: response.revision_number,
          task_status: 'processing',
          task_progress: 5,
        });

        // 如果没有taskId，直接结束
        if (!taskId) {
          get().updateMessage(assistantId, {
            content: response.message || '任务已提交',
            task_status: 'completed',
            task_progress: 100,
          });
          set({ sending: false });
          return;
        }

        startPollLoop(taskId, assistantId, generation);

      } catch (error) {
        get().updateMessage(assistantId, {
          content: `❌ 错误：${error instanceof Error ? error.message : '未知错误'}`,
          task_status: 'failed',
        });
        set({ sending: false });
      }
    },

    setAgent: (agent: AgentType) => {
      set({ currentAgent: agent });
    },

    clearChat: () => {
      pollGeneration++;
      // 立即解锁发送框：否则进行中的轮询链被代际令牌短路后 sending 永远为 true
      set({ messages: [], conversationId: undefined, lastOutputFileId: undefined, sending: false });
    },
  });
});

// ============ 轮询循环（sendMessage 与刷新恢复共用） ============
function startPollLoop(initialTaskId: string, assistantId: string, generation: number) {
  const get = () => useChatStore.getState();
  const set = useChatStore.setState;

  let taskId = initialTaskId;
  let pollCount = 0;
  let totalPolls = 0; // 跨自动修订的绝对上限，防止修订链无限轮询
  // 后端默认软超时是 30 分钟；前端必须覆盖同一时间窗并留出收尾余量，
  // 否则正常的 PPT 规划/生图任务会在 3 分钟时被错误显示为失败。
  const maxPolls = 1400; // 1400 x 1.5s = 35 分钟；自动修订链最多 4 代
  const maxTotalPolls = maxPolls * 4;
  const pollInterval = 1500;

  const giveUpTracking = (message: string) => {
    get().updateMessage(assistantId, {
      content: message,
      task_status: 'failed',
      task_error: message,
    });
    set({ sending: false });
  };

  const poll = async () => {
    try {
      if (generation !== pollGeneration) return; // 已被新消息/清空会话取代
      pollCount++;
      const task = await getTask(taskId!);

      // 更新进度
      const updates: Partial<ChatMessage> = {
        task_status: task.status,
        task_progress: task.progress,
        task_step: task.current_step,
      };

      totalPolls++;
      if (task.status === 'processing' || task.status === 'pending') {
        const stepText = task.current_step ? ` - ${task.current_step}` : '';
        updates.content = `🔧 正在处理中... ${task.progress}%${stepText}`;
        get().updateMessage(assistantId, updates);

        if (totalPolls >= maxTotalPolls) {
          giveUpTracking('⏰ 自动修订链处理时间过长，已停止跟踪。请稍后在任务历史中查看结果');
          return;
        }
        if (pollCount < maxPolls) {
          setTimeout(poll, pollInterval);
        } else {
          // 达到单任务轮询上限：停止跟踪并解锁输入框。
          giveUpTracking('⏰ 任务处理时间超过 35 分钟，已停止跟踪。请稍后在任务历史中查看结果');
        }
      } else if (task.status === 'completed' || task.status === 'failed') {
        const result = task.result as Record<string, unknown> | null;
        const settings = useSettingsStore.getState().settings;

        const autoRevisionTaskId = typeof result?.auto_revision_task_id === 'string'
          ? result.auto_revision_task_id
          : undefined;
        if (autoRevisionTaskId) {
          taskId = autoRevisionTaskId;
          pollCount = 0;
          get().updateMessage(assistantId, {
            content: '🔄 质量检查未通过，正在自动修订并重新检查...',
            task_id: autoRevisionTaskId,
            task_status: 'processing',
            task_progress: 5,
          });
          setTimeout(poll, pollInterval);
          return;
        }

        if (task.status === 'completed') {
          // 任务完成
          let resultText = '✅ 任务已完成！';

          if (result) {
            if (result.message) {
              resultText = `✅ ${result.message}`;
            }
            if (result.model_call && typeof result.model_call === 'object') {
              const call = result.model_call as Record<string, unknown>;
              const label = typeof call.model === 'string'
                ? call.model
                : (typeof call.provider === 'string' ? call.provider : '底层模型');
              resultText += call.success
                ? `\n\n模型解析：${label} 已调用`
                : '\n\n模型解析失败，已使用规则回退';
            }
            if (result.model_call && typeof result.model_call === 'object') {
              const call = result.model_call as Record<string, unknown>;
              if (call.success === false) {
                if (typeof call.attempts === 'number') {
                  resultText += `\n重试次数：${call.attempts}`;
                }
                if (typeof call.error === 'string' && call.error.trim()) {
                  resultText += `\n失败原因：${call.error.slice(0, 300)}`;
                }
              }
            }
            if (result.image_generation && typeof result.image_generation === 'object') {
              const imageGeneration = result.image_generation as Record<string, unknown>;
              const imageMessage = typeof imageGeneration.message === 'string'
                ? imageGeneration.message
                : '配图状态未知';
              const imageStatus = imageGeneration.status;
              if (imageStatus === 'success') {
                resultText += `\n\n配图：${imageMessage}`;
              } else if (imageStatus === 'partial') {
                resultText += `\n\n⚠ 配图：${imageMessage}`;
              } else if (imageStatus === 'failed' || imageStatus === 'unconfigured') {
                resultText += `\n\n⚠ 配图：${imageMessage}`;
              } else if (imageStatus === 'disabled' || imageStatus === 'skipped') {
                resultText += `\n\n配图：${imageMessage}`;
              }
            }
            if (task.output_files && task.output_files.length > 0) {
              resultText += '\n\n[输出文件]';
              for (const f of task.output_files) {
                resultText += `\n  • ${f.filename}`;
              }
            } else if (result.output_files && Array.isArray(result.output_files) && result.output_files.length > 0) {
              resultText += '\n\n[生成的文件]';
              for (const f of result.output_files) {
                resultText += `\n  • ${f}`;
              }
            }
            if (result.slides) {
              resultText += `\n📊 共 ${result.slides} 页`;
            }
          }

          get().updateMessage(assistantId, {
            content: resultText,
            task_status: 'completed',
            task_progress: 100,
            task_result: result || undefined,
            output_files: task.output_files,
          });
          const resultOutputId = typeof result?.output_file_id === 'string'
            ? result.output_file_id
            : undefined;
          const latestOutput = task.output_files?.[0]?.file_id || resultOutputId;
          if (latestOutput) {
            set({ lastOutputFileId: latestOutput });
          }

          // 完成通知 + 自动打开结果（尊重用户设置）
          if (settings.notifications) {
            sendNotification('任务已完成', resultText.replace(/\n/g, ' ').slice(0, 120)).catch(() => {});
          }
          if (settings.auto_open_results && task.output_files?.length) {
            const first = task.output_files[0];
            // 后端返回的 download_url 是相对 API 路径；Tauri/Web 开发环境的
            // 页面源与 Backend 不同，直接使用会请求到前端服务器。
            const url = getFileUrl(first.file_id);
            triggerDownload(url, first.filename);
          }
          set({ sending: false });
        } else {
          // 任务失败
          const errorMsg = task.error || (result?.error as string) || '未知错误';
          if (settings.notifications) {
            sendNotification('任务失败', errorMsg.slice(0, 120)).catch(() => {});
          }
          get().updateMessage(assistantId, {
            content: `❌ 任务失败：${errorMsg}`,
            task_status: 'failed',
            task_error: errorMsg,
          });
          set({ sending: false });
        }
      } else if (task.status === 'cancelled') {
        get().updateMessage(assistantId, {
          content: '🚫 任务已取消',
          task_status: 'cancelled',
        });
        set({ sending: false });
      }
    } catch (err) {
      console.error('Poll error:', err);
      if (pollCount < maxPolls) {
        setTimeout(poll, pollInterval * 2);
      } else {
        get().updateMessage(assistantId, {
          content: '⚠️ 无法获取任务状态，请在任务历史中查看',
        });
        set({ sending: false });
      }
    }
  };

  setTimeout(poll, pollInterval);
}

// ============ 状态持久化 + 刷新恢复 ============
if (typeof window !== 'undefined') {
  useChatStore.subscribe((state) => {
    persistChat(state);
  });

  // 刷新后：最近一条进行中的任务自动续接轮询（消息与结果不丢）
  const state = useChatStore.getState();
  const resumable = [...state.messages]
    .reverse()
    .find((m) => m.role === 'assistant' && m.task_status === 'processing' && m.task_id);
  if (resumable?.task_id) {
    const generation = ++pollGeneration;
    useChatStore.setState({ sending: true });
    useChatStore.getState().updateMessage(resumable.id, {
      content: '🔁 页面已刷新，正在恢复任务跟踪...',
    });
    startPollLoop(resumable.task_id, resumable.id, generation);
  }
}
