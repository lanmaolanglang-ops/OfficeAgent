import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({ getFileBytes: vi.fn() }));
vi.mock('./tauri', () => ({
  getDownloadDir: vi.fn(),
  isTauri: vi.fn(),
  saveFileDialog: vi.fn(),
  writeLocalFile: vi.fn(),
}));

import { getFileBytes } from './api';
import { downloadOutputFile } from './download';
import { getDownloadDir, isTauri, saveFileDialog, writeLocalFile } from './tauri';

const bytes = new Uint8Array([1, 2, 3, 4]);

describe('native desktop download', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getFileBytes).mockResolvedValue(bytes);
    vi.mocked(isTauri).mockReturnValue(true);
    vi.mocked(getDownloadDir).mockResolvedValue('C:\\Users\\tester\\Downloads');
  });

  it.each(['报告 中文.docx', '预算 (最终).xlsx', '路演 deck.pptx'])(
    'fetches bytes, opens native dialog, and writes %s',
    async (filename) => {
      const path = `C:\\Users\\tester\\Downloads\\${filename}`;
      vi.mocked(saveFileDialog).mockResolvedValue(path);

      await expect(downloadOutputFile('file-123', filename)).resolves.toEqual({ status: 'saved', path });
      expect(getFileBytes).toHaveBeenCalledWith('file-123');
      expect(saveFileDialog).toHaveBeenCalledWith(path, expect.any(Array));
      expect(writeLocalFile).toHaveBeenCalledWith(path, bytes);
    },
  );

  it('treats save-dialog cancellation as a normal outcome', async () => {
    vi.mocked(saveFileDialog).mockResolvedValue(null);
    await expect(downloadOutputFile('file-123', 'report.docx')).resolves.toEqual({ status: 'cancelled', path: null });
    expect(writeLocalFile).not.toHaveBeenCalled();
  });

  it('reports occupied or unwritable destinations without crashing', async () => {
    vi.mocked(saveFileDialog).mockResolvedValue('C:\\Downloads\\busy.docx');
    vi.mocked(writeLocalFile).mockRejectedValue(new Error('Access denied'));
    await expect(downloadOutputFile('file-123', 'busy.docx')).rejects.toThrow('文件可能正被占用');
  });

  it('does not open a dialog when the backend file is missing', async () => {
    vi.mocked(getFileBytes).mockRejectedValue(new Error('文件不存在或已被删除'));
    await expect(downloadOutputFile('missing', 'missing.docx')).rejects.toThrow('文件不存在');
    expect(saveFileDialog).not.toHaveBeenCalled();
  });
});
