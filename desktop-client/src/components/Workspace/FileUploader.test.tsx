/**
 * P5-17（审计 L27）：Tauri 拖拽路径回归。
 *
 * 修复前：`onTauriDragDrop` 回调只处理 `paths.slice(0, 1)`，拖入多个文件时
 * 其余文件被静默丢弃（浏览器 drop 路径却会处理全部）——同一交互在不同运行
 * 环境下行为不一致。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const tauriMock = vi.hoisted(() => ({
  handler: null as null | ((paths: string[]) => Promise<void> | void),
  readCalls: [] as string[],
}));

vi.mock('../../services/tauri', () => ({
  isTauri: () => true,
  onTauriDragDrop: async (handler: (paths: string[]) => Promise<void> | void) => {
    tauriMock.handler = handler;
    return () => { /* unlisten */ };
  },
  readLocalFile: async (path: string) => {
    tauriMock.readCalls.push(path);
    // 约定：路径含 missing 时模拟不可读
    return path.includes('missing') ? null : new Uint8Array([1, 2, 3]);
  },
}));

vi.mock('../../services/api', () => ({
  isAllowedFile: () => true,
  MAX_FILE_SIZE: 100 * 1024 * 1024,
  uploadFile: vi.fn(),
}));

import { uploadFile } from '../../services/api';
import FileUploader from './FileUploader';
import { useFileStore } from '../../stores';

function uploaded(fileId: string, filename: string) {
  return {
    file_id: fileId, filename, file_type: 'docx', size: 3,
    uploaded_at: '2026-01-01T00:00:00.000Z', id: fileId, name: filename, type: 'docx',
  };
}

describe('FileUploader Tauri drag-drop', () => {
  beforeEach(() => {
    tauriMock.handler = null;
    tauriMock.readCalls = [];
    vi.mocked(uploadFile).mockReset();
    useFileStore.setState({
      files: [], attachedFileIds: [], templateFileId: null,
      uploading: false, uploadProgress: 0,
    });
  });

  it('拖入多个文件时全部处理，不再静默丢弃', async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce(uploaded('a', 'a.docx'))
      .mockResolvedValueOnce(uploaded('b', 'b.docx'));

    render(<FileUploader />);
    await waitFor(() => expect(tauriMock.handler).not.toBeNull());

    await tauriMock.handler?.(['C:/docs/a.docx', 'C:/docs/b.docx']);

    await waitFor(() => expect(vi.mocked(uploadFile)).toHaveBeenCalledTimes(2));
    expect(tauriMock.readCalls).toEqual(['C:/docs/a.docx', 'C:/docs/b.docx']);
    // 两个文件都应进入 store 并都被附加（与 P5-5 的集合累加一致）
    expect(useFileStore.getState().files.map((f) => f.file_id)).toEqual(['a', 'b']);
    expect(useFileStore.getState().attachedFileIds).toEqual(['a', 'b']);
  });

  it('空拖拽不产生任何上传', async () => {
    render(<FileUploader />);
    await waitFor(() => expect(tauriMock.handler).not.toBeNull());

    await tauriMock.handler?.([]);

    expect(vi.mocked(uploadFile)).not.toHaveBeenCalled();
    expect(tauriMock.readCalls).toEqual([]);
  });

  it('读取失败的文件被跳过，不影响同批其它文件', async () => {
    vi.mocked(uploadFile).mockResolvedValue(uploaded('b', 'b.docx'));
    render(<FileUploader />);
    await waitFor(() => expect(tauriMock.handler).not.toBeNull());

    await tauriMock.handler?.(['C:/docs/missing.docx', 'C:/docs/b.docx']);

    await waitFor(() => expect(vi.mocked(uploadFile)).toHaveBeenCalledTimes(1));
    expect(useFileStore.getState().files.map((f) => f.file_id)).toEqual(['b']);
  });
});
