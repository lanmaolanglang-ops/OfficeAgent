/**
 * P5-5（审计 M36②）：fileStore 上传状态与附件集合回归。
 *
 * 修复前：
 * - `uploadFiles` 用 `[...state.files, ...uploaded]` 累积，同一文件重复上传会
 *   累积出多条（不去重）；
 * - `attachFile` 名为 attach 实为 `[fileId]` 覆盖式单选，多文件上传后只附加
 *   最后一个；
 * - `uploadFiles` 把同一个 onProgress 交给所有并发文件，进度互相覆盖。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../services/api', () => ({
  uploadFile: vi.fn(),
  listFilesPage: vi.fn(),
}));

import { uploadFile } from '../services/api';
import { useFileStore, mergeUploadedFiles, uploadedFileKey } from './fileStore';
import type { UploadedFile } from '../types';

function makeFile(id: string, name = `${id}.docx`): UploadedFile {
  return {
    file_id: id, filename: name, file_type: 'docx', size: 1,
    uploaded_at: '2026-01-01T00:00:00.000Z', id, name, type: 'docx',
  };
}

const initialState = {
  files: [], attachedFileIds: [], templateFileId: null,
  uploading: false, uploadProgress: 0,
};

describe('mergeUploadedFiles', () => {
  it('按 file_id 去重，重复上传覆盖旧条目而不是追加', () => {
    const merged = mergeUploadedFiles([makeFile('a')], [makeFile('a', 'renamed.docx'), makeFile('b')]);
    expect(merged).toHaveLength(2);
    expect(merged.find((f) => f.file_id === 'a')?.filename).toBe('renamed.docx');
  });

  it('无 id 的条目被忽略（不产生幽灵条目）', () => {
    const ghost = {
      file_id: '', filename: 'x', file_type: '', size: 0,
      uploaded_at: '', id: '', name: 'x', type: '',
    } as UploadedFile;
    expect(uploadedFileKey(ghost)).toBe('');
    expect(mergeUploadedFiles([], [ghost])).toHaveLength(0);
  });
});

describe('fileStore.uploadFiles', () => {
  beforeEach(() => {
    useFileStore.setState({ ...initialState });
    vi.mocked(uploadFile).mockReset();
  });

  it('重复上传同一文件不会累积重复条目', async () => {
    vi.mocked(uploadFile).mockResolvedValue(makeFile('same'));
    const store = useFileStore.getState();
    await store.uploadFiles([new File(['x'], 'a.docx')]);
    await useFileStore.getState().uploadFiles([new File(['x'], 'a.docx')]);
    expect(useFileStore.getState().files).toHaveLength(1);
  });

  it('并发上传时进度按整体折算，不被最后一个文件覆盖', async () => {
    let cbA: ((p: number) => void) | undefined;
    let cbB: ((p: number) => void) | undefined;
    vi.mocked(uploadFile)
      .mockImplementationOnce((_f, onProgress) => {
        cbA = onProgress;
        return Promise.resolve(makeFile('a'));
      })
      .mockImplementationOnce((_f, onProgress) => {
        cbB = onProgress;
        return Promise.resolve(makeFile('b'));
      });

    const seen: number[] = [];
    const promise = useFileStore.getState().uploadFiles(
      [new File(['x'], 'a.docx'), new File(['y'], 'b.docx')],
      (p) => seen.push(p),
    );

    cbA?.(100);
    cbB?.(0);
    // 整体进度 = (100 + 0) / 2 = 50，而不是被 B 的 0 覆盖
    expect(useFileStore.getState().uploadProgress).toBe(50);
    expect(seen.at(-1)).toBe(50);

    await promise;
    expect(useFileStore.getState().uploadProgress).toBe(100);
    expect(useFileStore.getState().uploading).toBe(false);
  });

  it('上传失败时清空 uploading 且不留下条目', async () => {
    vi.mocked(uploadFile).mockRejectedValue(new Error('boom'));
    await expect(useFileStore.getState().uploadFiles([new File(['x'], 'a.docx')]))
      .rejects.toThrow('boom');
    const state = useFileStore.getState();
    expect(state.uploading).toBe(false);
    expect(state.files).toHaveLength(0);
  });
});

describe('fileStore.attachFile', () => {
  beforeEach(() => {
    useFileStore.setState({ ...initialState, files: [makeFile('a'), makeFile('b')] });
  });

  it('多文件上传后逐个附加（集合累加，不再只留最后一个）', () => {
    useFileStore.getState().attachFile('a');
    useFileStore.getState().attachFile('b');
    expect(useFileStore.getState().attachedFileIds).toEqual(['a', 'b']);
  });

  it('重复附加同一文件不产生重复 id', () => {
    useFileStore.getState().attachFile('a');
    useFileStore.getState().attachFile('a');
    expect(useFileStore.getState().attachedFileIds).toEqual(['a']);
  });

  it('未上传成功的未知 id 不附加', () => {
    useFileStore.getState().attachFile('ghost');
    expect(useFileStore.getState().attachedFileIds).toEqual([]);
  });

  it('detach 只移除目标 id，其余保留', () => {
    useFileStore.getState().attachFile('a');
    useFileStore.getState().attachFile('b');
    useFileStore.getState().detachFile('a');
    expect(useFileStore.getState().attachedFileIds).toEqual(['b']);
  });

  it('removeFile 同步清理附件与模板引用', () => {
    useFileStore.getState().attachFile('a');
    useFileStore.getState().setTemplateFile('a');
    useFileStore.getState().removeFile('a');
    const state = useFileStore.getState();
    expect(state.attachedFileIds).toEqual([]);
    expect(state.templateFileId).toBeNull();
  });
});
