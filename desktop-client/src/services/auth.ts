/**
 * 统一的 API 凭据来源与注入点（P5-2 / 审计 M8）。
 *
 * 契约：
 * - 整个前端只有这一处持有 API 凭据；组件与 store 不得自行拼 `Authorization`。
 * - 每次请求读取"当前"凭据（`getAuthHeaders()` 实时取值），不在 client 创建时
 *   捕获旧值；凭据更新/清除后立即生效。
 * - 凭据只保存在内存里。产品当前没有登录流程，且审计明确要求"不要把 secret
 *   写进 localStorage，除非现有产品明确如此并已有保护设计"——当前没有，故不持久化。
 * - 后端契约（`office_agent/api/middleware/auth.py`）：`auth_enabled` 时接受
 *   `Authorization: Bearer <JWT>` 或 `X-API-Key: <key>`；未开启认证时匿名放行，
 *   此时没有凭据即为正常状态（本地免认证模式）。
 * - 凭据绝不出现在日志或错误消息中。
 */

export type AuthKind = 'bearer' | 'api-key';

export interface AuthCredential {
  kind: AuthKind;
  value: string;
}

let credential: AuthCredential | null = null;
const unauthorizedListeners = new Set<() => void>();

function normalize(value: string): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** 设置 Bearer（JWT）凭据；空值等同于清除。 */
export function setAuthToken(token: string): void {
  const value = normalize(token);
  credential = value ? { kind: 'bearer', value } : null;
}

/** 设置 X-API-Key 凭据；空值等同于清除。 */
export function setApiKey(key: string): void {
  const value = normalize(key);
  credential = value ? { kind: 'api-key', value } : null;
}

/** 清除凭据（登出 / 收到 401）。 */
export function clearAuthCredential(): void {
  credential = null;
}

/** 当前是否存在有效凭据。 */
export function hasAuthCredential(): boolean {
  return credential !== null;
}

/** 当前应注入的认证头；无凭据时返回空对象（本地免认证模式）。 */
export function getAuthHeaders(): Record<string, string> {
  if (!credential) return {};
  return credential.kind === 'bearer'
    ? { Authorization: `Bearer ${credential.value}` }
    : { 'X-API-Key': credential.value };
}

/**
 * 注册 401 回调（例如跳转登录 / 提示重新认证）。
 * 返回取消注册函数。
 */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => { unauthorizedListeners.delete(listener); };
}

/**
 * 后端返回 401：立即作废当前凭据，避免继续用旧凭据重试；
 * 然后通知订阅者。回调异常不影响请求路径。
 */
export function handleUnauthorized(): void {
  credential = null;
  for (const listener of Array.from(unauthorizedListeners)) {
    try {
      listener();
    } catch {
      // 订阅者自身的异常不得影响请求错误路径
    }
  }
}

/** 测试用：重置模块状态。 */
export function __resetAuthForTests(): void {
  credential = null;
  unauthorizedListeners.clear();
}
