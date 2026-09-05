import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  isAllowedFile, formatFileSize, mapBackendTask, MAX_FILE_SIZE,
  getEmbeddingModelSettings, saveEmbeddingModelSettings,
  testEmbeddingModelConnection,
} from './api';

describe('isAllowedFile', () => {
  it('允许 Office 文件', () => {
    expect(isAllowedFile('report.docx')).toBe(true);
    expect(isAllowedFile('slides.pptx')).toBe(true);
    expect(isAllowedFile('data.xlsx')).toBe(true);
  });

  it('拒绝不支持的类型', () => {
    expect(isAllowedFile('evil.exe')).toBe(false);
    expect(isAllowedFile('script.sh')).toBe(false);
    expect(isAllowedFile('archive.zip')).toBe(false);
  });

  it('扩展名大小写不敏感', () => {
    expect(isAllowedFile('REPORT.DOCX')).toBe(true);
    expect(isAllowedFile('Slides.Pptx')).toBe(true);
  });
});

describe('formatFileSize', () => {
  it('0 字节', () => {
    expect(formatFileSize(0)).toBe('0 B');
  });

  it('KB 级别', () => {
    expect(formatFileSize(1536)).toBe('1.5 KB');
  });

  it('MB 级别', () => {
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('非法或负数字节数按 0 处理', () => {
    expect(formatFileSize(-1)).toBe('0 B');
    expect(formatFileSize(Number.NaN)).toBe('0 B');
  });
});

describe('mapBackendTask', () => {
  it('已完成且缺少进度时回退到 100%', () => {
    expect(mapBackendTask({ id: 'done', status: 'completed' }).progress).toBe(100);
  });
});

describe('upload limits', () => {
  it('与后端默认的 100MB 限制保持一致', () => {
    expect(MAX_FILE_SIZE).toBe(100 * 1024 * 1024);
  });
});

describe('embedding settings API', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('backend_url', 'http://127.0.0.1:8765');
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads existing embedding settings', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      configured: true,
      provider: 'custom',
      model: 'text-embedding-3-small',
      base_url: 'https://example.com/v1',
      api_key_mask: 'sk-12****9x',
    }), { status: 200 }));

    const result = await getEmbeddingModelSettings();
    expect(result.configured).toBe(true);
    expect(result.api_key_mask).toBe('sk-12****9x');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      'http://127.0.0.1:8765/api/settings/embedding-model',
    );
  });

  it('saves provider config and preserves an unchanged secret', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      configured: true,
      provider: 'custom',
      model: 'text-embedding-3-small',
      base_url: 'https://example.com/v1',
      api_key_mask: 'sk-12****9x',
    }), { status: 200 }));

    await saveEmbeddingModelSettings({
      provider: 'custom',
      model: 'text-embedding-3-small',
      api_key: '',
      base_url: 'https://example.com/v1',
    });
    expect(fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8765/api/settings/embedding-model',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          provider: 'custom',
          model: 'text-embedding-3-small',
          api_key: '',
          base_url: 'https://example.com/v1',
        }),
      }),
    );
  });

  it('tests connection success and failure payloads', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      success: true,
      provider: 'custom',
      model: 'text-embedding-3-small',
      dimension: 1536,
      message: 'Embedding provider 连接成功',
    }), { status: 200 }));

    const success = await testEmbeddingModelConnection();
    expect(success.success).toBe(true);
    expect(success.dimension).toBe(1536);
  });
});
