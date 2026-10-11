import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, clearSession, SESSION_EXPIRED_EVENT } from './api';
const user = { id: 'test', email: 'synthetic@example.com', created_at: '2026-10-11T00:00:00' };
beforeEach(() => clearSession());
afterEach(() => vi.unstubAllGlobals());
it('login errors remain in the form and do not expire an unrelated page session', async () => {
  const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ detail: { code: 'invalid_credentials', message: '邮箱或密码错误' } }, { status: 401 })));
  await expect(api.login({ email: user.email, password: 'synthetic' })).rejects.toMatchObject({ status: 401 });
  expect(expired).not.toHaveBeenCalled(); window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
});
it('a protected 401 clears session and emits once without automatic retry', async () => {
  const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 401 })); vi.stubGlobal('fetch', fetcher);
  await expect(api.resumes()).rejects.toBeInstanceOf(ApiError);
  expect(expired).toHaveBeenCalledTimes(1); expect(fetcher).toHaveBeenCalledTimes(1);
  window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
});
it('stale CSRF and old 401 cannot replace or expire a newer login', async () => {
  let resolveCsrf!: (response: Response) => void;
  let resolveList!: (response: Response) => void;
  const fetcher = vi.fn((url: string, init: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return new Promise<Response>(resolve => { resolveCsrf = resolve; });
    if (url.endsWith('/auth/login')) return Promise.resolve(Response.json({ user, csrf_token: 'new-session-token' }));
    if (init?.method === 'PATCH') { expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('new-session-token'); return Promise.resolve(Response.json({ id: 'r' })); }
    return new Promise<Response>(resolve => { resolveList = resolve; });
  });
  vi.stubGlobal('fetch', fetcher);
  const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  const oldCsrf = api.csrf(); const oldList = api.resumes().catch(() => undefined);
  await api.login({ email: user.email, password: 'synthetic' });
  resolveCsrf(Response.json({ csrf_token: 'old-session-token' })); resolveList(Response.json({}, { status: 401 }));
  await Promise.all([oldCsrf, oldList]); await api.rename('r', { file_name: 'new.pdf' });
  expect(expired).not.toHaveBeenCalled(); window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
});
it('rate limit errors are actionable and never retried automatically', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 429 })); vi.stubGlobal('fetch', fetcher);
  await expect(api.resumes()).rejects.toMatchObject({ status: 429, message: expect.stringContaining('稍后') });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
