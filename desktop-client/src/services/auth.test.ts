/**
 * P5-2（审计 M8）：API 认证注入统一性回归。
 *
 * 修复前：`api.ts` 的 `request()` / `uploadFile()` 完全不注入 Authorization，
 * 前端也没有任何凭据来源——后端一旦开启 JWT/APIKey 认证，所有接口 401。
 *
 * 修复后：全前端只有一个凭据来源（services/auth.ts），`request()`（fetch）
 * 与 `uploadFile()`（XHR）共用它；每次请求读取当前凭据；401 立即作废凭据；
 * 凭据不出现在错误消息中。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  setAuthToken, setApiKey, clearAuthCredential, hasAuthCredential,
  getAuthHeaders, onUnauthorized, handleUnauthorized, __resetAuthForTests,
} from './auth';
import { getModelSettings, uploadFile } from './api';

describe('auth credential store', () => {
  beforeEach(() => { __resetAuthForTests(); });

  it('无凭据时不注入任何认证头（本地免认证模式）', () => {
    expect(getAuthHeaders()).toEqual({});
    expect(hasAuthCredential()).toBe(false);
  });

  it('Bearer 与 API Key 各自映射到后端契约的头', () => {
    setAuthToken('jwt-token');
    expect(getAuthHeaders()).toEqual({ Authorization: 'Bearer jwt-token' });
    setApiKey('key-123');
    expect(getAuthHeaders()).toEqual({ 'X-API-Key': 'key-123' });
  });

  it('读取的是当前凭据，不是捕获的旧值', () => {
    setAuthToken('first');
    expect(getAuthHeaders().Authorization).toBe('Bearer first');
    setAuthToken('second');
    expect(getAuthHeaders().Authorization).toBe('Bearer second');
  });

  it('清除后不再注入（不得继续发旧凭据）', () => {
    setAuthToken('jwt-token');
    clearAuthCredential();
    expect(getAuthHeaders()).toEqual({});
    setAuthToken('jwt-token');
    setAuthToken('');
    expect(getAuthHeaders()).toEqual({});
  });

  it('空白凭据视为未设置', () => {
    setAuthToken('   ');
    expect(hasAuthCredential()).toBe(false);
    setApiKey('\t');
    expect(getAuthHeaders()).toEqual({});
  });

  it('401 处理会作废凭据并通知订阅者', () => {
    const seen: number[] = [];
    const off = onUnauthorized(() => seen.push(1));
    setAuthToken('jwt-token');
    handleUnauthorized();
    expect(hasAuthCredential()).toBe(false);
    expect(getAuthHeaders()).toEqual({});
    expect(seen).toEqual([1]);
    off();
    handleUnauthorized();
    expect(seen).toEqual([1]);
  });

  it('订阅者异常不影响请求错误路径', () => {
    onUnauthorized(() => { throw new Error('listener boom'); });
    setAuthToken('jwt-token');
    expect(() => handleUnauthorized()).not.toThrow();
    expect(hasAuthCredential()).toBe(false);
  });
});

describe('request() authentication injection', () => {
  beforeEach(() => {
    __resetAuthForTests();
    localStorage.clear();
    localStorage.setItem('backend_url', 'http://127.0.0.1:8765');
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetAuthForTests();
  });

  function okResponse() {
    return new Response(JSON.stringify({ configured: false, models: [] }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  }

  it('有凭据时携带 Authorization', async () => {
    setAuthToken('jwt-token');
    vi.mocked(fetch).mockResolvedValue(okResponse());
    await getModelSettings();
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-token');
  });

  it('API Key 模式携带 X-API-Key', async () => {
    setApiKey('key-123');
    vi.mocked(fetch).mockResolvedValue(okResponse());
    await getModelSettings();
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('key-123');
  });

  it('无凭据时不携带认证头', async () => {
    vi.mocked(fetch).mockResolvedValue(okResponse());
    await getModelSettings();
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(headers['X-API-Key']).toBeUndefined();
  });

  it('调用方自定义 header 不得覆盖认证头', async () => {
    setAuthToken('jwt-token');
    vi.mocked(fetch).mockResolvedValue(okResponse());
    await getModelSettings();
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-token');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('401 会作废凭据，且错误消息不含凭据', async () => {
    setAuthToken('super-secret-token');
    vi.mocked(fetch).mockResolvedValue(new Response('{"detail":"unauthorized"}', {
      status: 401, headers: { 'Content-Type': 'application/json' },
    }));
    await expect(getModelSettings()).rejects.toThrow(/401/);
    expect(hasAuthCredential()).toBe(false);
    try {
      await getModelSettings();
    } catch (error) {
      expect(String((error as Error).message)).not.toContain('super-secret-token');
    }
  });
});

describe('uploadFile() authentication injection', () => {
  class FakeXHR {
    static instances: FakeXHR[] = [];
    static nextStatus = 200;
    static nextBody = JSON.stringify({
      success: true,
      data: { file_id: 'file-1', filename: 'a.docx', size: 1 },
    });
    upload = { addEventListener: () => { /* progress 回调在测试中不需要 */ } };
    status = FakeXHR.nextStatus;
    responseText = FakeXHR.nextBody;
    timeout = 0;
    headers: Record<string, string> = {};
    private listeners: Record<string, Array<() => void>> = {};

    constructor() { FakeXHR.instances.push(this); }
    open() { /* noop */ }
    setRequestHeader(name: string, value: string) { this.headers[name] = value; }
    addEventListener(type: string, cb: () => void) {
      (this.listeners[type] ||= []).push(cb);
    }
    send() {
      queueMicrotask(() => (this.listeners.load || []).forEach((cb) => cb()));
    }
    abort() { /* noop */ }
  }

  beforeEach(() => {
    __resetAuthForTests();
    FakeXHR.instances = [];
    FakeXHR.nextStatus = 200;
    FakeXHR.nextBody = JSON.stringify({
      success: true,
      data: { file_id: 'file-1', filename: 'a.docx', size: 1 },
    });
    localStorage.clear();
    localStorage.setItem('backend_url', 'http://127.0.0.1:8765');
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetAuthForTests();
  });

  it('上传路径与 request() 共用同一凭据来源', async () => {
    setApiKey('key-123');
    const uploaded = await uploadFile(new File(['x'], 'a.docx'));
    expect(uploaded.file_id).toBe('file-1');
    expect(FakeXHR.instances[0].headers['X-API-Key']).toBe('key-123');
    // multipart 的 Content-Type 必须留给浏览器（含 boundary）
    expect(FakeXHR.instances[0].headers['Content-Type']).toBeUndefined();
  });

  it('无凭据时上传不携带认证头', async () => {
    await uploadFile(new File(['x'], 'a.docx'));
    expect(FakeXHR.instances[0].headers).toEqual({});
  });

  it('上传 401 会作废凭据', async () => {
    setAuthToken('jwt-token');
    FakeXHR.nextStatus = 401;
    FakeXHR.nextBody = '{"detail":"unauthorized"}';
    await expect(uploadFile(new File(['x'], 'a.docx'))).rejects.toThrow(/认证失败/);
    expect(hasAuthCredential()).toBe(false);
  });
});
