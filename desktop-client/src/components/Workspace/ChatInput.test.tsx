// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChatInput from './ChatInput';

vi.mock('../../stores', () => ({
  useChatStore: () => ({ currentAgent: 'auto', setAgent: vi.fn() }),
}));

afterEach(cleanup);

describe('ChatInput IME submission', () => {
  it('does not submit Enter while a CJK composition is active', () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByRole('textbox', { name: '任务说明' });

    fireEvent.change(input, { target: { value: '中文输入' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue('中文输入');
  });

  it('keeps the keyCode 229 fallback and submits a normal Enter', async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByRole('textbox', { name: '任务说明' });

    fireEvent.change(input, { target: { value: '中文输入' } });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();

    await user.type(input, '{Enter}');
    expect(onSend).toHaveBeenCalledWith('中文输入');
  });
});
