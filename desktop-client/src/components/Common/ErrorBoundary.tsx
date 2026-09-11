import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * 根级 React 渲染树错误兜底。
 *
 * 【能捕获】—— React render tree 内的同步异常：
 *   - 子组件 render / 生命周期（constructor、render、componentDidMount…）抛出的异常
 *   - 子组件构造函数抛出的异常
 *
 * 【不能捕获】—— ErrorBoundary 天生覆盖不到，**此处不假装捕获**：
 *   - 事件处理器（onClick 等）里的异常：React 不会把它冒泡回 render tree
 *   - setTimeout / Promise / async 回调里的异常 → 会变成 unhandledrejection
 *   - 原生 DOM 事件监听器、SSR 阶段的异常
 *   - ErrorBoundary 自身 render 抛出的异常（会继续向上冒泡）
 *
 * 因此这里**刻意不注册 window.onerror / unhandledrejection 来"兜住一切"**：
 * 那会把本组件覆盖不到的错误伪装成已处理，反而掩盖真实故障。
 * 全局错误上报若需要，应作为独立能力实现，而不是塞进本组件。
 */

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** 自定义兜底 UI；不传则用内置兜底。 */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** 错误上报 / 日志钩子。 */
  onError?: (error: Error, info: ErrorInfo) => void;
  /** 变化时自动重置（例如路由 location），避免旧错误长期卡住界面。 */
  resetKeys?: unknown[];
  /** 兜底 UI 标题。 */
  title?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

function arraysShallowEqual(a?: unknown[], b?: unknown[]): boolean {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  return a.every((item, i) => Object.is(item, b[i]));
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 开发环境输出完整堆栈；生产环境只留一行摘要，
    // 不把内部实现细节暴露给最终用户。
    if (import.meta.env.DEV) {
      console.error('[ErrorBoundary] 渲染树异常:', error, info?.componentStack);
    } else {
      console.error('[ErrorBoundary] 渲染树异常:', error?.message ?? String(error));
    }
    this.props.onError?.(error, info);
  }

  componentDidUpdate(prev: ErrorBoundaryProps): void {
    // 只在 resetKeys 真的变化时重置。捕获态下 children 不再渲染，
    // 因此不会形成"渲染 → 抛错 → 重置 → 再渲染"的无限循环。
    if (this.state.error && !arraysShallowEqual(prev.resetKeys, this.props.resetKeys)) {
      this.reset();
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }
    return (
      <ErrorFallback
        error={error}
        onReset={this.reset}
        title={this.props.title ?? '界面出错了'}
      />
    );
  }
}

interface ErrorFallbackProps {
  error: Error;
  onReset: () => void;
  title: string;
}

export function ErrorFallback({ error, onReset, title }: ErrorFallbackProps) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex min-h-full w-full items-center justify-center bg-bg p-6"
    >
      <div className="w-full max-w-lg rounded-xl border border-line bg-card p-6 shadow-sm">
        <h1 className="text-lg font-semibold text-fg">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-fg-muted">
          界面在渲染时遇到错误，已停止渲染该部分内容以避免整页白屏。
          你可以先重试；若反复出现，请重新加载应用。
        </p>

        {/* 完整堆栈只在开发模式展示，生产环境不向最终用户暴露内部细节。 */}
        {import.meta.env.DEV && (
          <pre className="mt-4 max-h-40 overflow-auto rounded-lg bg-muted p-3 text-xs leading-relaxed text-fg-soft">
            {error?.message}
            {error?.stack ? `\n${error.stack}` : ''}
          </pre>
        )}

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={onReset}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-card transition-colors hover:bg-brand-hover"
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-muted"
          >
            重新加载
          </button>
        </div>
      </div>
    </div>
  );
}

export default ErrorBoundary;
