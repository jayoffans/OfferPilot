import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, clearSession, SESSION_EXPIRED_EVENT } from './api';
const user = { id: 'test', email: 'synthetic@example.com', created_at: '2026-10-11T00:00:00' };
beforeEach(() => clearSession());
afterEach(() => vi.unstubAllGlobals());
it('account data writes carry CSRF and explicit intent while deletion status 401 stays on the result page', async () => {
  const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  const bodies: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'account-csrf' }));
    if (url.endsWith('/me/deletion/status')) return Promise.resolve(Response.json({}, { status: 401 }));
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('account-csrf');
    bodies.push(JSON.parse(String(init?.body))); return Promise.resolve(Response.json({ id: 'synthetic' }));
  }));
  await api.createExport({ password: 'Synthetic-password', idempotency_key: 'export-key' });
  await api.createDeletion({ password: 'Synthetic-password', confirmation: '注销账户', idempotency_key: 'delete-key' });
  await expect(api.deletionStatus()).rejects.toMatchObject({ status: 401 });
  expect(bodies).toEqual([{ password: 'Synthetic-password', idempotency_key: 'export-key' }, { password: 'Synthetic-password', confirmation: '注销账户', idempotency_key: 'delete-key' }]);
  expect(expired).not.toHaveBeenCalled(); window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
});
it('dashboard reads are bounded, uncached and expire a rejected session without retry', async () => {
  const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
  const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 401 })); vi.stubGlobal('fetch', fetcher);
  await expect(api.dashboard()).rejects.toMatchObject({ status: 401 });
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/dashboard?limit=5'); expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store' });
  expect(fetcher).toHaveBeenCalledOnce(); expect(expired).toHaveBeenCalledOnce(); window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
});
it('application create, edit and delete carry CSRF, revision and explicit idempotency intent', async () => {
  const bodies: unknown[] = [];
  const methods: string[] = [];
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'application-csrf' }));
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('application-csrf');
    expect(init?.credentials).toBe('same-origin'); bodies.push(JSON.parse(init?.body as string)); methods.push(init?.method || 'GET');
    return Promise.resolve(init?.method === 'DELETE' ? new Response(null, { status: 204 }) : Response.json({ id: 'synthetic' }));
  });
  vi.stubGlobal('fetch', fetcher);
  const create = { job_id: 'synthetic-job', job_revision: 2, idempotency_key: 'create-key', reapply: false, match_id: null };
  const update = { revision: 1, idempotency_key: 'edit-key', status: 'submitted' as const, submitted_at: null, notes: '用户确认', next_action: null };
  const remove = { revision: 2, idempotency_key: 'delete-key', confirmed: true as const };
  await api.createApplication(create); await api.updateApplication('application-id', update); await api.deleteApplication('application-id', remove);
  expect(bodies).toEqual([create, update, remove]); expect(methods).toEqual(['POST', 'PUT', 'DELETE']); expect(fetcher).toHaveBeenCalledTimes(4);
});
it('matching and preference writes carry CSRF, revisions and no fabricated profile payload', async () => {
  const bodies: unknown[] = [];
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'matching-csrf' }));
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('matching-csrf');
    bodies.push(JSON.parse(init?.body as string));
    return Promise.resolve(Response.json({ id: 'synthetic' }));
  });
  vi.stubGlobal('fetch', fetcher);
  await api.createMatch('synthetic-job', { profile_revision: 3, job_revision: 2, idempotency_key: 'matching-attempt' });
  await api.savePreferences({ revision: 3, preferences: { locations: ['上海'], work_types: [], target_roles: [], salary: null, weekly_days: null, internship_months: null } });
  expect(bodies).toEqual([{ profile_revision: 3, job_revision: 2, idempotency_key: 'matching-attempt' }, { revision: 3, preferences: { locations: ['上海'], work_types: [], target_roles: [], salary: null, weekly_days: null, internship_months: null } }]);
  expect(fetcher).toHaveBeenCalledTimes(3);
});
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
it('profile writes use the generated contract, current revision and server session CSRF', async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'synthetic-csrf' }));
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf');
    expect(init?.credentials).toBe('same-origin');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({ revision: 3, education: [], skills: [{ name: 'Python', level: null }], projects: [], experiences: [] });
    return Promise.resolve(Response.json({ revision: 4 }));
  });
  vi.stubGlobal('fetch', fetcher);
  await api.saveProfile({ revision: 3, education: [], skills: [{ name: 'Python', level: null }], projects: [], experiences: [] });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe('/api/v1/profile');
});
it('job writes carry CSRF, revision and idempotency and are never implicitly retried', async () => {
  const id = '11111111-1111-4111-8111-111111111111';
  const requests: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'synthetic-csrf' }));
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf');
    expect(init?.credentials).toBe('same-origin');
    requests.push({ url, method: init?.method || 'GET', body: JSON.parse(String(init?.body)) });
    return Promise.resolve(init?.method === 'DELETE' ? new Response(null, { status: 204 }) : Response.json({ id, revision: 1 }));
  }));
  await api.createJob({ title: '合成职位', work_type: 'unknown', jd_text: 'Java优先', idempotency_key: 'synthetic-create', requirements: [] });
  await api.favoriteJob(id, 3, true); await api.deleteJob(id, 4);
  expect(requests).toEqual([
    { url: '/api/v1/jobs', method: 'POST', body: { title: '合成职位', work_type: 'unknown', jd_text: 'Java优先', idempotency_key: 'synthetic-create', requirements: [] } },
    { url: `/api/v1/jobs/${id}/favorite`, method: 'PATCH', body: { revision: 3, is_favorite: true } },
    { url: `/api/v1/jobs/${id}`, method: 'DELETE', body: { revision: 4 } },
  ]);
});

it('extraction mutations preserve idempotency, resource version and run revision with session CSRF', async () => {
  const id = '11111111-1111-4111-8111-111111111111';
  const sourceVersion = 'a'.repeat(64);
  const requests: { url: string; body: unknown }[] = [];
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/auth/csrf')) return Promise.resolve(Response.json({ csrf_token: 'synthetic-csrf' }));
    expect(init?.method).toBe('POST'); expect(init?.credentials).toBe('same-origin');
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf');
    requests.push({ url, body: JSON.parse(String(init?.body)) });
    return Promise.resolve(Response.json({ id, mode: 'mock', test_result: true }));
  });
  vi.stubGlobal('fetch', fetcher);
  await api.createExtraction(id, { idempotency_key: 'synthetic-request', source_version: sourceVersion, mode: 'mock' });
  await api.cancelExtraction(id, 3); await api.retryExtraction(id, 4);
  expect(requests).toEqual([
    { url: `/api/v1/resumes/${id}/extraction-runs`, body: { idempotency_key: 'synthetic-request', source_version: sourceVersion, mode: 'mock' } },
    { url: `/api/v1/extraction-runs/${id}/cancel`, body: { revision: 3 } },
    { url: `/api/v1/extraction-runs/${id}/retry`, body: { revision: 4 } },
  ]);
  expect(fetcher).toHaveBeenCalledTimes(4);
});
