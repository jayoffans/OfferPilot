// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { allowedRoute, proxyApi } from './proxy';

const id = '9e9bff19-a61d-4857-b128-e6225e2832cc';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe('same-origin API boundary', () => {
  it('exposes only the implemented auth, file and profile API', () => {
    expect(allowedRoute('GET', `resumes/${id}/download`)).toBe(true);
    for (const target of ['../health', 'auth/invite', '__e2e/ready', `resumes/${id}/profile`, 'https://example.com']) expect(allowedRoute('GET', target)).toBe(false);
    expect(allowedRoute('POST', `resumes/${id}/download`)).toBe(false);
    expect(allowedRoute('GET', 'profile')).toBe(true);
    expect(allowedRoute('PUT', 'profile')).toBe(true);
    expect(allowedRoute('POST', `profile/candidates/${id}/review`)).toBe(true);
    for (const target of ['profile/other-user', 'profile/candidates/not-a-uuid/review', `profile/candidates/${id}`, 'profile/ingest']) expect(allowedRoute('POST', target)).toBe(false);
    expect(allowedRoute('DELETE', 'profile')).toBe(false);
    for (const target of [`resumes/${id}/extraction`, `extraction-runs/${id}`, `extraction-runs/${id}/candidates`]) expect(allowedRoute('GET', target)).toBe(true);
    for (const target of [`resumes/${id}/extraction-runs`, `extraction-runs/${id}/cancel`, `extraction-runs/${id}/retry`]) expect(allowedRoute('POST', target)).toBe(true);
    for (const target of ['extraction-runs', 'extraction-runs/not-a-uuid/cancel', `extraction-runs/${id}/execute`, `resumes/${id}/extraction`]) expect(allowedRoute('POST', target)).toBe(false);
    expect(allowedRoute('DELETE', `extraction-runs/${id}`)).toBe(false);
    for (const method of ['GET', 'PUT', 'DELETE']) expect(allowedRoute(method, 'ai/deepseek-key')).toBe(true);
    expect(allowedRoute('POST', 'ai/deepseek-key')).toBe(false);
    expect(allowedRoute('GET', `resumes/${id}/extraction-preview`)).toBe(true);
    expect(allowedRoute('GET', 'ai/deepseek-key/other-user')).toBe(false);
  });
  it('rejects hostile origins before contacting the backend', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const response = await proxyApi(new Request('http://127.0.0.1:3000/api/v1/auth/login', { method: 'POST', headers: { Origin: 'https://attacker.example' } }), ['auth', 'login']);
    expect(response.status).toBe(403); expect(fetcher).not.toHaveBeenCalled();
  });
  it('allows only concrete job resource and review routes', () => {
    expect(allowedRoute('POST', 'jobs/parse-preview')).toBe(true);
    expect(allowedRoute('PUT', `jobs/${id}`)).toBe(true);
    expect(allowedRoute('PATCH', `jobs/${id}/favorite`)).toBe(true);
    expect(allowedRoute('GET', `jobs/${id}/requirements`)).toBe(true);
    expect(allowedRoute('POST', `jobs/${id}/requirements/${id}/review`)).toBe(true);
    for (const target of ['jobs/fetch-url', 'jobs/not-a-uuid', `jobs/${id}/submit`, `jobs/${id}/match`, `jobs/${id}/requirements/not-a-uuid/review`]) expect(allowedRoute('POST', target)).toBe(false);
    expect(allowedRoute('GET', `jobs/${id}/requirements/${id}`)).toBe(false);
  });
  it('forwards only whitelisted unique job-list parameters, without enabling arbitrary targets', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] })); vi.stubGlobal('fetch', fetcher);
    const response = await proxyApi(new Request('http://127.0.0.1:3000/api/v1/jobs?q=Java%20%E4%BC%98%E5%85%88&is_favorite=true&page=2'), ['jobs']);
    expect(response.status).toBe(200);
    expect(new URL(fetcher.mock.calls[0][0]).searchParams.get('q')).toBe('Java 优先');
    for (const query of ['url=https://evil.example', 'q=a&q=b', 'user_id=other', 'page=1&redirect=evil']) expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/jobs?${query}`), ['jobs'])).status).toBe(404);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/jobs/${id}?q=a`), ['jobs', id])).status).toBe(404);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('forwards HttpOnly session and CSRF without unrelated browser cookies', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      const headers = new Headers(init.headers);
      expect(headers.get('cookie')).toBe('offerpilot_session=abc_123');
      expect(headers.get('x-csrf-token')).toBe('safe-token');
      expect(headers.get('origin')).toBe('http://127.0.0.1:3000');
      return new Response(null, { status: 204, headers: { 'Set-Cookie': 'offerpilot_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0' } });
    }));
    const response = await proxyApi(new Request('http://127.0.0.1:3000/api/v1/auth/logout', { method: 'POST', headers: { Cookie: 'analytics=value; offerpilot_session=abc_123; other=value', 'X-CSRF-Token': 'safe-token', Origin: 'http://127.0.0.1:3000' } }), ['auth', 'logout']);
    expect(response.status).toBe(204); expect(response.headers.get('set-cookie')).toContain('HttpOnly'); expect(response.headers.get('cache-control')).toBe('no-store');
  });
  it('returns an actionable safe error when backend is stopped', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('secret internal path')));
    const response = await proxyApi(new Request('http://127.0.0.1:3000/api/v1/me'), ['me']);
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain('secret internal path');
  });
  it('rejects oversized uploads before upstream request', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const response = await proxyApi(new Request('http://127.0.0.1:3000/api/v1/resumes', { method: 'POST', headers: { 'Content-Length': String(12 * 1024 * 1024) } }), ['resumes']);
    expect(response.status).toBe(413); expect(fetcher).not.toHaveBeenCalled();
  });
  it('cancels a stalled body reader within the bounded request timeout', async () => {
    vi.useFakeTimers();
    const canceled = vi.fn();
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const body = new ReadableStream<Uint8Array>({ cancel: canceled });
    const request = new Request('http://127.0.0.1:3000/api/v1/resumes', { method: 'POST', body, duplex: 'half' } as RequestInit);
    const pending = proxyApi(request, ['resumes']);
    await vi.advanceTimersByTimeAsync(55_000);
    expect((await pending).status).toBe(504); expect(canceled).toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
