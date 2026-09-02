import { describe, it, expect } from 'vitest';
import { isAllowedFile, formatFileSize, mapBackendTask, MAX_FILE_SIZE } from './api';

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
