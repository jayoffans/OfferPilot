const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const READ = new RegExp(`^(me|auth/csrf|profile|ai/deepseek-key|resumes|resumes/${UUID}(/(lifecycle|download|extraction|extraction-preview))?|extraction-runs/${UUID}(/candidates)?|jobs|jobs/${UUID}(/requirements|/matches(/latest|/${UUID})?)?)$`, 'i');
const JOB_MATCHES = new RegExp(`^jobs/${UUID}/matches$`, 'i');
const APPLICATION = new RegExp(`^applications/${UUID}$`, 'i');
const APPLICATION_EVENTS = new RegExp(`^applications/${UUID}/events$`, 'i');
const JOB = new RegExp(`^jobs/${UUID}$`, 'i');
const JOB_FAVORITE = new RegExp(`^jobs/${UUID}/favorite$`, 'i');
const JOB_REQUIREMENTS = new RegExp(`^jobs/${UUID}/requirements$`, 'i');
const JOB_REQUIREMENT = new RegExp(`^jobs/${UUID}/requirements/${UUID}$`, 'i');
const JOB_REVIEW = new RegExp(`^jobs/${UUID}/requirements/${UUID}/review$`, 'i');
const RESOURCE = new RegExp(`^resumes/${UUID}$`, 'i');
const REVIEW = new RegExp(`^profile/candidates/${UUID}/review$`, 'i');
const EXTRACTION_WRITE = new RegExp(`^(resumes/${UUID}/extraction-runs|extraction-runs/${UUID}/(cancel|retry))$`, 'i');
const EXPORT_READ = new RegExp(`^me/export/${UUID}(/download)?$`, 'i');
const MAX_BODY_BYTES = 11 * 1024 * 1024;

function error(status: number, code: string, message: string) {
  return Response.json({ detail: { code, message } }, { status, headers: { 'Cache-Control': 'no-store' } });
}
export function allowedRoute(method: string, path: string): boolean {
  if (method === 'GET' && (EXPORT_READ.test(path) || ['me/exports', 'me/deletion/status'].includes(path))) return true;
  if (method === 'POST' && ['me/export', 'me/deletion'].includes(path)) return true;
  if (method === 'GET' && (path === 'dashboard' || path === 'applications' || APPLICATION.test(path) || APPLICATION_EVENTS.test(path))) return true;
  if (method === 'POST' && path === 'applications') return true;
  if ((method === 'PUT' || method === 'DELETE') && APPLICATION.test(path)) return true;
  if (method === 'GET') return READ.test(path);
  if (method === 'POST') return ['auth/register', 'auth/login', 'auth/logout', 'resumes', 'jobs', 'jobs/parse-preview'].includes(path) || JOB_MATCHES.test(path) || REVIEW.test(path) || EXTRACTION_WRITE.test(path) || JOB_REQUIREMENTS.test(path) || JOB_REVIEW.test(path);
  if (method === 'PUT') return path === 'profile' || path === 'profile/preferences' || path === 'ai/deepseek-key' || JOB.test(path) || JOB_REQUIREMENTS.test(path) || JOB_REQUIREMENT.test(path);
  if (method === 'DELETE' && path === 'ai/deepseek-key') return true;
  if (method === 'PATCH' && JOB_FAVORITE.test(path)) return true;
  if (method === 'DELETE' && (JOB.test(path) || JOB_REQUIREMENT.test(path))) return true;
  return (method === 'DELETE' || method === 'PATCH') && RESOURCE.test(path);
}
export function allowedOrigin(request: Request): boolean {
  if (request.headers.get('sec-fetch-site') === 'cross-site') return false;
  const origin = request.headers.get('origin');
  if (!origin) return true; // Native CLI remains subject to backend CSRF/auth.
  try {
    const origins: unknown = JSON.parse(process.env.OFFERPILOT_WEB_ORIGINS || '["http://localhost:3000","http://127.0.0.1:3000"]');
    return Array.isArray(origins) && origins.includes(origin);
  } catch { return false; }
}
async function bodyWithinLimit(request: Request | Response, signal: AbortSignal, maximum = MAX_BODY_BYTES): Promise<Uint8Array | undefined> {
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener('abort', cancel, { once: true });
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      const result = await reader.read();
      if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      if (result.done) break;
      size += result.value.byteLength;
      if (size > maximum) { await reader.cancel(); throw new RangeError('body_too_large'); }
      chunks.push(result.value);
    }
  } finally { signal.removeEventListener('abort', cancel); reader.releaseLock(); }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}
export async function proxyApi(request: Request, segments: string[]): Promise<Response> {
  const path = segments.join('/');
  const exportDownload = EXPORT_READ.test(path) && path.endsWith('/download');
  const query = new URL(request.url).searchParams;
  const jobListQuery = request.method === 'GET' && path === 'jobs' && [...query.keys()].every(key => ['q', 'work_type', 'is_favorite', 'review_status', 'sort', 'page', 'page_size'].includes(key) && query.getAll(key).length === 1);
  const matchHistoryQuery = request.method === 'GET' && JOB_MATCHES.test(path) && [...query.keys()].every(key => key === 'limit' && query.getAll(key).length === 1);
  const applicationListQuery = request.method === 'GET' && path === 'applications' && [...query.keys()].every(key => ['q', 'status', 'job_id', 'next_action', 'overdue', 'sort', 'page', 'page_size'].includes(key) && query.getAll(key).length === 1);
  const dashboardQuery = request.method === 'GET' && path === 'dashboard' && [...query.keys()].every(key => key === 'limit' && query.getAll(key).length === 1);
  const applicationEventsQuery = request.method === 'GET' && APPLICATION_EVENTS.test(path) && [...query.keys()].every(key => ['page', 'page_size'].includes(key) && query.getAll(key).length === 1);
  if (!allowedRoute(request.method, path) || (query.size > 0 && !jobListQuery && !matchHistoryQuery && !applicationListQuery && !applicationEventsQuery && !dashboardQuery)) return error(404, 'route_not_found', '请求的接口不存在。');
  if (!allowedOrigin(request)) return error(403, 'untrusted_origin', '请求来源未获授权。');
  if (Number(request.headers.get('content-length')) > MAX_BODY_BYTES) return error(413, 'body_too_large', '文件过大，请使用不超过 10 MiB 的 PDF。');
  let target: URL;
  try {
    target = new URL(process.env.OFFERPILOT_API_URL || 'http://127.0.0.1:8000');
    if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password || target.pathname !== '/' || target.search || target.hash) throw new Error('invalid_target');
  } catch { return error(503, 'invalid_api_configuration', '服务配置不可用，请联系维护者。'); }
  const headers = new Headers();
  for (const name of ['content-type', 'x-csrf-token', 'origin', 'sec-fetch-site']) {
    const value = request.headers.get(name); if (value) headers.set(name, value);
  }
  const cookies = request.headers.get('cookie')?.split(';').map(x => x.trim()) || [];
  const session = cookies.find(x => /^offerpilot_session=[A-Za-z0-9_-]+$/.test(x));
  const deletionStatus = path === 'me/deletion/status' ? cookies.find(x => /^offerpilot_deletion_status=[A-Za-z0-9_-]+$/.test(x)) : undefined;
  const permittedCookies = [session, deletionStatus].filter(Boolean);
  if (permittedCookies.length) headers.set('cookie', permittedCookies.join('; '));
  const controller = new AbortController();
  const cancel = () => controller.abort();
  request.signal.addEventListener('abort', cancel, { once: true });
  if (request.signal.aborted) cancel();
  const timeout = setTimeout(() => controller.abort(), 55_000);
  try {
    const body = ['GET', 'HEAD'].includes(request.method) ? undefined : await bodyWithinLimit(request, controller.signal);
    const upstream = await fetch(new URL(`/api/v1/${path}${(jobListQuery || matchHistoryQuery || applicationListQuery || applicationEventsQuery || dashboardQuery) && query.size ? `?${query.toString()}` : ''}`, target), {
      method: request.method, headers, body: body as BodyInit | undefined, signal: controller.signal, redirect: 'manual', cache: 'no-store',
    });
    const responseHeaders = new Headers({ 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    for (const name of ['content-type', 'content-disposition', 'retry-after']) {
      const value = upstream.headers.get(name); if (value) responseHeaders.set(name, value);
    }
    for (const cookie of upstream.headers.getSetCookie()) {
      if (cookie.startsWith('offerpilot_session=') || (['me/deletion', 'me/deletion/status'].includes(path) && cookie.startsWith('offerpilot_deletion_status='))) responseHeaders.append('set-cookie', cookie);
    }
    const location = upstream.headers.get('location');
    if (location && (/^\/api\/v1\/resumes\/[0-9a-f-]+\/lifecycle$/i.test(location) || /^\/api\/v1\/me\/(export\/[0-9a-f-]{36}|deletion\/status)$/i.test(location))) responseHeaders.set('location', location);
    // Bounded backend files are buffered before response so a disconnect does
    // not leave an upstream stream or timeout running after the route returns.
    let content: BodyInit | null;
    if (exportDownload && upstream.ok) {
      const configured = Number(process.env.OFFERPILOT_EXPORT_MAX_BYTES || 67108864);
      const maximum = Number.isInteger(configured) && configured >= 1048576 && configured <= 1073741824 ? configured : 67108864;
      if (Number(upstream.headers.get('content-length')) > maximum) {
        await upstream.body?.cancel();
        return error(413, 'export_resource_limit', '导出文件超过下载上限，请联系维护者处理。');
      }
      content = (await bodyWithinLimit(upstream, controller.signal, maximum)) as BodyInit ?? null;
    } else content = upstream.status === 204 ? null : await upstream.arrayBuffer();
    return new Response(content, { status: upstream.status, headers: responseHeaders });
  } catch (e) {
    if (e instanceof RangeError) return exportDownload ? error(413, 'export_resource_limit', '导出文件超过下载上限，请联系维护者处理。') : error(413, 'body_too_large', '文件过大，请使用不超过 10 MiB 的 PDF。');
    if (controller.signal.aborted) return error(504, 'backend_timeout', '服务响应超时，操作结果尚未确认。请先刷新状态，避免重复上传。');
    return error(502, 'backend_unavailable', '暂时无法连接服务，请确认后端正在运行后重试。');
  } finally { clearTimeout(timeout); request.signal.removeEventListener('abort', cancel); }
}
