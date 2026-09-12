/**
 * P1-21：chatStore 异步竞态隔离。
 *
 * 修复前的真实竞态（本文件即为其最小可复现形态）：
 *   A 请求在 `await sendChatMessage(...)` 期间，用户清空会话并发出新请求 B；
 *   之后 A 的响应/轮询结果迟到返回时**未做代际校验**就写入共享状态：
 *     - `set({ conversationId: A.conversation_id })` 覆盖新会话的会话 id；
 *     - `set({ sending: false })` 把仍在进行的 B 的发送框解锁；
 *     - `set({ lastOutputFileId })` 把 A 的产物写进新会话；
 *     - `clearAttachments()` 清掉用户在等待期间新附加的文件。
 *
 * 说明：本 store 是"单会话"模型（没有多会话列表），并发入口由 `sending`
 * 门禁 + `clearChat()` 解锁构成，因此测试用 `clearChat()` 作为"切到新会话"。
 * 修复方向是**请求级 identity（generation）守卫**，不是全局互斥锁；
 * 消息本身按 id 更新（updateMessage），天然只作用于自己那条占位消息。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  sendChatMessage: vi.fn(),
  getTask: vi.fn(),
  getFileUrl: vi.fn((id: string) => `http://local/api/file/download/${id}`),
}));

const tauri = vi.hoisted(() => ({
  sendNotification: vi.fn(() => Promise.resolve()),
}));

const fileStore = vi.hoisted(() => ({
  templateFileId: undefined as string | undefined,
  attachedFileIds: [] as string[],
  clearAttachments: vi.fn(() => {
    fileStore.attachedFileIds = [];
    fileStore.templateFileId = undefined;
  }),
}));

const settingsStore = vi.hoisted(() => ({
  settings: { notifications: false, auto_open_results: false },
}));

vi.mock('../services/api', () => api);
vi.mock('../services/tauri', () => tauri);
vi.mock('./fileStore', () => ({ useFileStore: { getState: () => fileStore } }));
vi.mock('./settingsStore', () => ({
  useSettingsStore: { getState: () => settingsStore },
}));

type Deferred = {
  promise: Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
};

function deferred(): Deferred {
  let resolve!: (value: unknown) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<unknown>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function loadStore() {
  vi.resetModules();
  const mod = await import('./chatStore');
  return mod.useChatStore;
}

function taskPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'task',
    status: 'processing',
    progress: 10,
    current_step: 'step',
    result: null,
    output_files: [],
    error: undefined,
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  api.sendChatMessage.mockReset();
  api.getTask.mockReset();
  fileStore.attachedFileIds = [];
  fileStore.templateFileId = undefined;
  fileStore.clearAttachments.mockClear();
  tauri.sendNotification.mockClear();
  settingsStore.settings = { notifications: false, auto_open_results: false };
});

afterEach(() => {
  vi.useRealTimers();
});

describe('chatStore 请求级竞态隔离', () => {
  it('迟到的 A 响应不得覆盖新会话 B 的 conversationId / sending / 附件', async () => {
    const responseA = deferred();
    api.sendChatMessage.mockReturnValueOnce(responseA.promise); // A：挂起，模拟未完成

    const store = await loadStore();

    // 1. A 发起请求（HTTP 未返回）
    const pendingA = store.getState().sendMessage('A 的消息');
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().sending).toBe(true);
    expect(store.getState().messages.map((m) => m.content)).toContain('A 的消息');

    // 2. 用户清空会话（切到新会话）
    store.getState().clearChat();
    expect(store.getState().messages).toEqual([]);
    expect(store.getState().conversationId).toBeUndefined();

    // 3. 新请求 B：返回 conversation_id 并进入轮询
    fileStore.attachedFileIds = ['new-file'];
    api.sendChatMessage.mockResolvedValueOnce({
      task_id: 'task-B', conversation_id: 'conv-B', agent: 'WordAgent',
    });
    api.getTask.mockResolvedValue(taskPayload({ id: 'task-B' }));

    await store.getState().sendMessage('B 的消息');
    await vi.advanceTimersByTimeAsync(0);

    expect(store.getState().conversationId).toBe('conv-B');
    expect(store.getState().sending).toBe(true);
    expect(store.getState().messages.map((m) => m.content)).toContain('B 的消息');
    expect(fileStore.clearAttachments).toHaveBeenCalledTimes(1); // 只应由 B 清一次

    // 4. A 迟到返回：不得污染 B 的任何状态
    responseA.resolve({ task_id: undefined, conversation_id: 'conv-A', message: 'A 已提交' });
    await vi.advanceTimersByTimeAsync(0);
    await pendingA;

    expect(store.getState().conversationId).toBe('conv-B');
    expect(store.getState().sending).toBe(true); // B 仍在进行，不能被 A 解锁
    expect(store.getState().messages.map((m) => m.content)).not.toContain('A 的消息');
    expect(fileStore.clearAttachments).toHaveBeenCalledTimes(1); // A 不得再清一次
  });

  it('迟到的 A 轮询结果不得清空 B 的 sending，也不得写入 A 的产物', async () => {
    const pollA = deferred();
    api.sendChatMessage
      .mockResolvedValueOnce({ task_id: 'task-A', conversation_id: 'conv-A' })
      .mockResolvedValueOnce({ task_id: 'task-B', conversation_id: 'conv-B' });
    api.getTask
      .mockReturnValueOnce(pollA.promise) // A 的轮询挂起
      .mockResolvedValue(taskPayload({ id: 'task-B' }));

    const store = await loadStore();

    // 1. A 发起并进入轮询，第一次 getTask 挂起
    await store.getState().sendMessage('A');
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.getTask).toHaveBeenCalledWith('task-A');

    // 2. 切到新会话并发起 B
    store.getState().clearChat();
    await store.getState().sendMessage('B');
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().sending).toBe(true);

    // 3. A 的轮询迟到返回"已完成"
    pollA.resolve(taskPayload({
      id: 'task-A',
      status: 'completed',
      progress: 100,
      result: { message: 'A 完成' },
      output_files: [{ file_id: 'file-A', filename: 'A.docx' }],
    }));
    await vi.advanceTimersByTimeAsync(0);

    expect(store.getState().sending).toBe(true); // B 仍在进行
    expect(store.getState().lastOutputFileId).toBeUndefined(); // 不得写入 A 的产物
    expect(store.getState().conversationId).toBe('conv-B'); // 不得回落成 A
  });

  it('迟到的 A 失败不得解锁 B 的发送框', async () => {
    const failA = deferred();
    api.sendChatMessage
      .mockReturnValueOnce(failA.promise)
      .mockResolvedValueOnce({ task_id: 'task-B', conversation_id: 'conv-B' });
    api.getTask.mockResolvedValue(taskPayload({ id: 'task-B' }));

    const store = await loadStore();

    const pendingA = store.getState().sendMessage('A');
    await vi.advanceTimersByTimeAsync(0);
    store.getState().clearChat();
    await store.getState().sendMessage('B');
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().sending).toBe(true);

    failA.reject(new Error('A 网络失败'));
    await vi.advanceTimersByTimeAsync(0);
    await pendingA;

    expect(store.getState().sending).toBe(true);
  });

  it('同一会话连续两请求：进行中时第二次发送被忽略（不产生错配占位消息）', async () => {
    api.sendChatMessage.mockResolvedValue({ task_id: 'task-1', conversation_id: 'conv-1' });
    api.getTask.mockResolvedValue(taskPayload({ id: 'task-1' }));

    const store = await loadStore();
    await store.getState().sendMessage('第一条');
    await vi.advanceTimersByTimeAsync(0);

    const before = store.getState().messages.length;
    await store.getState().sendMessage('第二条');
    await vi.advanceTimersByTimeAsync(0);

    expect(store.getState().messages.length).toBe(before);
    expect(store.getState().messages.map((m) => m.content)).not.toContain('第二条');
    expect(api.sendChatMessage).toHaveBeenCalledTimes(1);
  });

  it('正常完成：占位消息与最终消息一一对应，sending 归位', async () => {
    api.sendChatMessage.mockResolvedValue({
      task_id: 'task-1', conversation_id: 'conv-1', agent: 'WordAgent',
    });
    api.getTask.mockResolvedValue(taskPayload({
      id: 'task-1',
      status: 'completed',
      progress: 100,
      current_step: 'done',
      result: { message: '排版完成' },
      output_files: [{ file_id: 'f1', filename: 'out.docx' }],
    }));

    const store = await loadStore();
    await store.getState().sendMessage('帮我排版');
    await vi.advanceTimersByTimeAsync(1500);

    const state = store.getState();
    expect(state.sending).toBe(false);
    expect(state.conversationId).toBe('conv-1');
    expect(state.lastOutputFileId).toBe('f1');

    const assistants = state.messages.filter((m) => m.role === 'assistant');
    expect(assistants).toHaveLength(1);
    expect(assistants[0].task_id).toBe('task-1');
    expect(assistants[0].task_status).toBe('completed');
    expect(assistants[0].content).toContain('排版完成');
    expect(assistants[0].content).not.toContain('正在理解');

    const users = state.messages.filter((m) => m.role === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].content).toBe('帮我排版');
  });

  it('轮询进行中的进度更新只作用于自己的占位消息', async () => {
    api.sendChatMessage.mockResolvedValue({ task_id: 'task-1', conversation_id: 'conv-1' });
    api.getTask.mockResolvedValue(taskPayload({
      id: 'task-1', status: 'processing', progress: 42, current_step: '解析文档',
    }));

    const store = await loadStore();
    await store.getState().sendMessage('任务');
    const assistantId = store.getState().messages[1].id;

    await vi.advanceTimersByTimeAsync(1500);

    const assistant = store.getState().messages.find((m) => m.id === assistantId);
    expect(assistant?.task_progress).toBe(42);
    expect(assistant?.task_step).toBe('解析文档');
    expect(assistant?.task_status).toBe('processing');
    expect(store.getState().sending).toBe(true);
  });

  it('轮询链在清空会话后不再回写任何消息', async () => {
    api.sendChatMessage.mockResolvedValue({ task_id: 'task-1', conversation_id: 'conv-1' });
    api.getTask.mockResolvedValue(taskPayload({
      id: 'task-1', status: 'completed', progress: 100, result: { message: 'done' },
    }));

    const store = await loadStore();
    await store.getState().sendMessage('任务');
    store.getState().clearChat();

    await vi.advanceTimersByTimeAsync(5000);

    expect(store.getState().messages).toEqual([]);
    expect(store.getState().sending).toBe(false);
    expect(store.getState().conversationId).toBeUndefined();
  });
});
