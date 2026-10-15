// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { allowedRoute, proxyApi } from './proxy';

const id = '9e9bff19-a61d-4857-b128-e6225e2832cc';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe('same-origin API boundary', () => {
  it('allows concrete application resources and only unique bounded list and timeline queries', async () => {
    for (const path of ['applications', `applications/${id}`, `applications/${id}/events`]) expect(allowedRoute('GET', path)).toBe(true);
    expect(allowedRoute('POST', 'applications')).toBe(true);
    for (const method of ['PUT', 'DELETE']) expect(allowedRoute(method, `applications/${id}`)).toBe(true);
    for (const method of ['POST', 'PATCH']) expect(allowedRoute(method, `applications/${id}`)).toBe(false);
    for (const path of ['applications/not-a-uuid', `applications/${id}/submit`, `applications/${id}/events/${id}`]) expect(allowedRoute('GET', path)).toBe(false);
    expect(allowedRoute('PUT', `applications/${id}/events`)).toBe(false);
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json({ items: [], total: 0 }))); vi.stubGlobal('fetch', fetcher);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/applications?job_id=${id}&status=interview&sort=next_action_asc&page=2`), ['applications'])).status).toBe(200);
    expect(new URL(fetcher.mock.calls[0][0]).searchParams.get('job_id')).toBe(id);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/applications/${id}/events?page=2&page_size=20`), ['applications', id, 'events'])).status).toBe(200);
    expect(new URL(fetcher.mock.calls[1][0]).search).toBe('?page=2&page_size=20');
    for (const query of ['user_id=other', 'q=a&q=b', 'page=1&url=http://localhost']) expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/applications?${query}`), ['applications'])).status).toBe(404);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/applications/${id}?page=1`), ['applications', id])).status).toBe(404);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/applications/${id}/events?user_id=other`), ['applications', id, 'events'])).status).toBe(404);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('allows only concrete private matching and preference routes', async () => {
    for (const path of [`jobs/${id}/matches`, `jobs/${id}/matches/latest`, `jobs/${id}/matches/${id}`]) expect(allowedRoute('GET', path)).toBe(true);
    expect(allowedRoute('POST', `jobs/${id}/matches`)).toBe(true);
    expect(allowedRoute('PUT', 'profile/preferences')).toBe(true);
    for (const method of ['PUT', 'PATCH', 'DELETE']) expect(allowedRoute(method, `jobs/${id}/matches`)).toBe(false);
    expect(allowedRoute('POST', `jobs/${id}/matches/latest`)).toBe(false);
    expect(allowedRoute('GET', 'jobs/not-a-uuid/matches')).toBe(false);
    expect(allowedRoute('POST', 'profile/preferences')).toBe(false);
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], total: 0 })); vi.stubGlobal('fetch', fetcher);
    expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/jobs/${id}/matches?limit=10`), ['jobs', id, 'matches'])).status).toBe(200);
    expect(new URL(fetcher.mock.calls[0][0]).search).toBe('?limit=10');
    for (const query of ['user_id=other', 'limit=1&limit=2', 'limit=10&url=http://localhost']) expect((await proxyApi(new Request(`http://127.0.0.1:3000/api/v1/jobs/${id}/matches?${query}`), ['jobs', id, 'matches'])).status).toBe(404);
    expect(fetcher).toHaveBeenCalledOnce();
  });
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
