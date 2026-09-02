// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FileManager from './index';
import type { UploadedFile } from '../../types';

const loadFiles = vi.fn(async () => undefined);
const listDeletedFilesPage = vi.fn(async (
  _params?: { page?: number; page_size?: number },
): Promise<{ files: UploadedFile[]; total: number; page: number; page_size: number }> => ({
  files: [], total: 0, page: 1, page_size: 200,
}));
const restoreDeletedFile = vi.fn(async (_fileId: string) => undefined);
const deleteFile = vi.fn(async (_fileId: string, _permanent?: boolean) => undefined);

vi.mock('../../stores', () => ({
  useFileStore: () => ({ files: [], loadFiles }),
}));

vi.mock('../../services/api', () => ({
  deleteFile: (fileId: string, permanent?: boolean) => deleteFile(fileId, permanent),
  getFileUrl: (id: string) => `/api/file/download/${id}`,
  listDeletedFilesPage: (params?: { page?: number; page_size?: number }) => listDeletedFilesPage(params),
  listFileVersions: vi.fn(async () => []),
  restoreDeletedFile: (fileId: string) => restoreDeletedFile(fileId),
  restoreFileVersion: vi.fn(async () => undefined),
}));

const deletedFile = {
  file_id: 'file_deleted',
  filename: '董事会复盘.docx',
  file_type: 'word',
  size: 2048,
  uploaded_at: '2026-08-30T08:00:00Z',
  deleted_at: '2026-08-31T08:00:00Z',
  id: 'file_deleted',
  name: '董事会复盘.docx',
  type: 'word',
};

describe('FileManager recycle bin', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listDeletedFilesPage.mockResolvedValue({ files: [deletedFile], total: 1, page: 1, page_size: 200 });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('loads deleted files and restores one to the active file list', async () => {
    const user = userEvent.setup();
    render(<FileManager />);

    await user.click(screen.getByRole('tab', { name: /回收站/ }));
    expect(await screen.findByText('董事会复盘.docx')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '恢复 董事会复盘.docx' }));

    await waitFor(() => expect(restoreDeletedFile).toHaveBeenCalledWith('file_deleted'));
    expect(loadFiles).toHaveBeenCalled();
    expect(await screen.findByRole('status')).toHaveTextContent('已恢复到文件中心');
  });

  it('requires explicit confirmation before permanent deletion', async () => {
    const user = userEvent.setup();
    render(<FileManager />);

    await user.click(screen.getByRole('tab', { name: /回收站/ }));
    await screen.findByText('董事会复盘.docx');
    await user.click(screen.getByRole('button', { name: '永久删除 董事会复盘.docx' }));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('无法撤销'));
    await waitFor(() => expect(deleteFile).toHaveBeenCalledWith('file_deleted', true));
  });
});
