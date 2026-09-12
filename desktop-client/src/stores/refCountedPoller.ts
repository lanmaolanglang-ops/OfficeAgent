/**
 * 进程级引用计数定时轮询。
 *
 * ## 为什么不用模块级变量
 *
 * 把 `{ timer, refs }` 放在模块作用域时，句柄会随"模块被重新求值"失联：
 * 新模块实例看到的 `pollTimer` 是 `null`，而旧 `setInterval` 仍然活着且
 * **没有任何引用能清除它**。P1-25 实测（加载第二个模块实例）：
 *
 * - 同时存在 **2 个** interval；
 * - 6 秒内任务列表请求从 3 次变成 **6 次**（轮询成倍放大）；
 * - 连调 10 次 `stopPolling()` 之后仍有 **1 个 interval 永久存活**。
 *
 * 把槽位锚定到 `globalThis` 上之后，任意模块实例都复用/回收同一个
 * interval，"同一时刻至多一个轮询器"成为可验证的不变式；所有订阅者释放后
 * 定时器必然被清除，不存在不可回收的孤儿。
 *
 * 注意：这里**不是**给聊天加全局互斥锁，只是让定时器句柄可回收。
 */

/** 槽位键前缀，避免与其它 globalThis 使用方撞名 */
const SLOT_PREFIX = 'officeagent.polling.';

export interface PollSlot {
  timer: ReturnType<typeof setInterval> | null;
  subscribers: number;
  /** 最新订阅者提供的 tick，避免定时器回调闭包指向旧模块的 store 实例 */
  tick: (() => void) | null;
}

function slotKey(key: string): string {
  return SLOT_PREFIX + key;
}

function holder(): Record<string, PollSlot | undefined> {
  return globalThis as unknown as Record<string, PollSlot | undefined>;
}

/** 取得（必要时创建）某个轮询键的进程级槽位。 */
export function getPollSlot(key: string): PollSlot {
  const store = holder();
  const id = slotKey(key);
  let slot = store[id];
  if (!slot) {
    slot = { timer: null, subscribers: 0, tick: null };
    store[id] = slot;
  }
  return slot;
}

/**
 * 增加一个订阅者。
 *
 * @returns 是否**新建**了定时器（便于调用方只在首次订阅时做一次立即执行）
 */
export function acquirePolling(
  key: string,
  intervalMs: number,
  tick: () => void,
): boolean {
  const slot = getPollSlot(key);
  // 始终指向最新订阅者的回调：模块重复求值后，定时器必须调用新实例的
  // store，而不是旧闭包。
  slot.tick = tick;
  slot.subscribers += 1;
  if (slot.timer !== null) {
    return false;
  }
  slot.timer = setInterval(() => {
    slot.tick?.();
  }, intervalMs);
  return true;
}

/** 释放一个订阅者；归零时清除定时器。多余的 release 不会误杀其它订阅者。 */
export function releasePolling(key: string): void {
  const slot = getPollSlot(key);
  slot.subscribers = Math.max(0, slot.subscribers - 1);
  if (slot.subscribers > 0) {
    return;
  }
  if (slot.timer !== null) {
    clearInterval(slot.timer);
    slot.timer = null;
  }
}

/** 仅用于测试与诊断：当前活跃订阅数。 */
export function pollingSubscribers(key: string): number {
  return getPollSlot(key).subscribers;
}

/** 仅用于测试与诊断：当前是否持有存活定时器。 */
export function pollingActive(key: string): boolean {
  return getPollSlot(key).timer !== null;
}

/** 仅用于测试清理：清空定时器与订阅计数。 */
export function resetPolling(key: string): void {
  const slot = getPollSlot(key);
  if (slot.timer !== null) {
    clearInterval(slot.timer);
  }
  slot.timer = null;
  slot.subscribers = 0;
  slot.tick = null;
}
