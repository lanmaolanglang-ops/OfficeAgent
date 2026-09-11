// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ErrorBoundary, { ErrorFallback } from './ErrorBoundary';

/**
 * React 在捕获渲染异常时会主动 console.error 两次（错误本体 + 组件栈）。
 * 这里把它 stub 掉以免污染测试输出，同时用它断言 componentDidCatch 确实被调用。
 * 注意：只是静音日志，**没有**吞掉任何断言。
 */
let errorLog: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  errorLog.mockRestore();
  vi.unstubAllEnvs();
});

function Boom({ message = '渲染爆炸' }: { message?: string }): never {
  throw new Error(message);
}

describe('ErrorBoundary 根级兜底', () => {
  it('子组件 render 抛异常时渲染兜底 UI，而不是整页空白', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('界面出错了')).toBeInTheDocument();
    expect(screen.queryByText('正常内容')).not.toBeInTheDocument();
  });

  it('componentDidCatch 被调用（错误被真正捕获并记录）', () => {
    const onError = vi.fn();
    render(
      <ErrorBoundary onError={onError}>
        <Boom message="特定错误文本" />
      </ErrorBoundary>,
    );

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBeInstanceOf(Error);
    expect((onError.mock.calls[0][0] as Error).message).toBe('特定错误文本');
    expect(errorLog).toHaveBeenCalled();
  });

  it('正常组件不受影响，照常渲染', () => {
    render(
      <ErrorBoundary>
        <p>正常内容</p>
      </ErrorBoundary>,
    );

    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(errorLog).not.toHaveBeenCalled();
  });

  it('重试后重新渲染 children；仍抛错则再次兜底而非崩溃', () => {
    const { rerender } = render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();

    // 重试：清空错误状态后重新挂载 children
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    // 此时 children 仍会抛错 -> 应再次回到兜底 UI，而不是抛出到测试外
    expect(screen.getByRole('alert')).toBeInTheDocument();

    // 把 children 换成正常内容后再次重试，应恢复
    rerender(
      <ErrorBoundary>
        <p>恢复正常</p>
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(screen.getByText('恢复正常')).toBeInTheDocument();
  });

  it('重试能真正恢复：错误源已修复后 children 正常显示', () => {
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('第一次挂');
      return <p>已恢复</p>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(screen.getByText('已恢复')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('重新加载按钮触发 window.location.reload', () => {
    const reload = vi.fn();
    const original = window.location.reload;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    });

    try {
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      );
      fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...window.location, reload: original },
      });
    }
  });

  it('捕获后不再重复渲染抛错的子树（不无限重渲染）', async () => {
    let throwCount = 0;
    function CountedBoom(): never {
      throwCount += 1;
      throw new Error('boom');
    }

    const { rerender } = render(
      <ErrorBoundary>
        <CountedBoom />
      </ErrorBoundary>,
    );

    // React 为收集组件栈会同步重放 render 若干次，次数由 React 版本决定，
    // 但必须是**有限**的——这才是"不无限重渲染"的判据。
    expect(throwCount).toBeGreaterThan(0);
    expect(throwCount).toBeLessThanOrEqual(8);
    const afterCatch = throwCount;

    // 同一棵子树重复 rerender：处于兜底态时 children 不再挂载，计数不增长
    for (let i = 0; i < 5; i += 1) {
      rerender(
        <ErrorBoundary>
          <CountedBoom />
        </ErrorBoundary>,
      );
    }
    expect(throwCount).toBe(afterCatch);

    // 再等若干个宏/微任务周期，确认没有异步重渲染循环
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(throwCount).toBe(afterCatch);
  });

  it('resetKeys 变化时自动重置', () => {
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('挂了');
      return <p>路由恢复</p>;
    }

    const { rerender } = render(
      <ErrorBoundary resetKeys={['/dashboard']}>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();

    shouldThrow = false;
    // resetKeys 未变：保持兜底态
    rerender(
      <ErrorBoundary resetKeys={['/dashboard']}>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();

    // resetKeys 变化：自动重置
    rerender(
      <ErrorBoundary resetKeys={['/settings']}>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByText('路由恢复')).toBeInTheDocument();
  });

  it('支持自定义 fallback，并拿到 error 与 reset', () => {
    render(
      <ErrorBoundary
        fallback={(error, reset) => (
          <div>
            <span>自定义:{error.message}</span>
            <button type="button" onClick={reset}>
              自定义重试
            </button>
          </div>
        )}
      >
        <Boom message="自定义路径" />
      </ErrorBoundary>,
    );

    expect(screen.getByText('自定义:自定义路径')).toBeInTheDocument();
    expect(screen.queryByText('界面出错了')).not.toBeInTheDocument();
  });

  it('对照组：没有 ErrorBoundary 时，抛错子树会让整棵树被卸载且错误冒到调用方（白屏成因）', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);

    // 无兜底时：错误直接冒出 render()，React 卸载整棵树，页面什么都不剩（即白屏）。
    // 这正是 ErrorBoundary 要消除的行为。
    let escaped: unknown = null;
    try {
      render(
        <>
          <p>健康内容</p>
          <Boom />
        </>,
        { container },
      );
    } catch (e) {
      escaped = e;
    }

    expect(escaped).toBeInstanceOf(Error);
    expect((escaped as Error).message).toBe('渲染爆炸');
    expect(container.textContent).toBe('');
    expect(screen.queryByText('健康内容')).not.toBeInTheDocument();
  });

  it('自定义标题生效', () => {
    render(
      <ErrorBoundary title="模块加载失败">
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText('模块加载失败')).toBeInTheDocument();
  });
});

describe('ErrorFallback 自身健壮性', () => {
  it('ErrorBoundary 自身渲染不抛异常', () => {
    expect(() =>
      render(
        <ErrorBoundary>
          <p>ok</p>
        </ErrorBoundary>,
      ),
    ).not.toThrow();
  });

  it('错误对象缺少 stack 时兜底 UI 仍可渲染', () => {
    const err = new Error('无堆栈');
    err.stack = undefined;
    expect(() => render(<ErrorFallback error={err} onReset={() => {}} title="出错了" />)).not.toThrow();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('生产模式下不向用户暴露内部堆栈', () => {
    vi.stubEnv('DEV', false);
    const err = new Error('敏感内部信息');
    err.stack = 'Error: 敏感内部信息\n    at 内部路径/内部文件.tsx:1:1';

    const { container } = render(
      <ErrorFallback error={err} onReset={() => {}} title="出错了" />,
    );

    expect(container.textContent).not.toContain('内部路径');
    expect(container.querySelector('pre')).toBeNull();
  });
});
