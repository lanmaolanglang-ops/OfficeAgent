// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Sidebar from './Sidebar';

vi.mock('../../stores', () => ({
  useBackendStore: () => ({ connected: true, degraded: false }),
  useSettingsStore: (selector: (state: object) => unknown) => selector({
    settings: { backend_url: 'http://127.0.0.1:8765' },
  }),
}));

afterEach(cleanup);

describe('Sidebar production version', () => {
  it('renders the version injected from the Python release source', () => {
    render(<MemoryRouter><Sidebar /></MemoryRouter>);
    expect(screen.getByText(new RegExp(`v${__APP_VERSION__}`))).toBeInTheDocument();
  });
});
