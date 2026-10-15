import type { components } from './schema';

type Schemas = components['schemas'];
export type User = Schemas['UserResponse'];
export type Resume = Schemas['ResumeUploadResponse'];
export type ResumeLifecycle = Schemas['ResumeLifecycleResponse'];
export type CareerProfile = Schemas['CareerProfileResponse'];
export type ProfileSave = Schemas['ProfileSaveRequest'];
export type ProfileCandidate = Schemas['ProfileCandidateResponse'];
export type ProfileReview = Schemas['ProfileCandidateReviewRequest'];
export type EducationInput = Schemas['EducationInput'];
export type SkillInput = Schemas['SkillInput'];
export type ProjectInput = Schemas['ProjectInput'];
export type ExperienceInput = Schemas['ExperienceInput'];
export type ExtractionRun = Schemas['ExtractionRunResponse'];
export type ExtractionCapability = Schemas['ExtractionCapabilityResponse'];
export type ExtractionCreate = Schemas['ExtractionRunCreateRequest'];
export type ExtractionCandidates = Schemas['ExtractionRunCandidatesResponse'];
export type KeyStatus = Schemas['KeyStatus'];
export type ExtractionPreview = Schemas['ExtractionPreview'];
export type Job = Schemas['JobDetailResponse'];
export type JobList = Schemas['JobListResponse'];
export type JobListItem = Schemas['JobListItem'];
export type JobCreate = Schemas['JobCreateRequest'];
export type JobUpdate = Schemas['JobSaveRequest'];
export type JobRequirementInput = Schemas['JobRequirementInput'];
export type JobRequirement = Schemas['JobRequirementResponse'];
export type JobParsePreview = Schemas['JobParsePreviewResponse'];
type LoginResponse = Schemas['LoginResponse'];

let csrfToken: string | null = null;
let csrfPending: { controller: AbortController; promise: Promise<string> } | null = null;
let sessionGeneration = 0;
export const SESSION_EXPIRED_EVENT = 'offerpilot:session-expired';
export const SESSION_RESET_EVENT = 'offerpilot:session-reset';
export function clearSession() {
  sessionGeneration++; csrfToken = null; csrfPending?.controller.abort(); csrfPending = null;
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(SESSION_RESET_EVENT));
}
function expireSession(generation: number) {
  if (generation !== sessionGeneration) return;
  clearSession();
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

const messages: Record<number, string> = {
  400: '请求未完成，请检查输入后重试。',
  401: '登录信息无效或会话已失效，请重新登录。',
  403: '请求未获授权，请刷新页面后重试。',
  404: '找不到这份简历，可能已被删除或当前账户无法访问。',
  409: '当前操作存在冲突，请刷新状态后再试。',
  410: '原件已不可用，请联系维护者检查文件存储。',
  413: '文件过大，请选择不超过 10 MiB 的 PDF。',
  415: '文件格式不支持，请使用文本型 PDF。',
  422: '输入或 PDF 内容不符合要求，请检查后重试。',
  429: '操作过于频繁，请稍后再试。系统不会自动重复提交。',
  500: '服务暂时遇到问题，请稍后重试。',
  502: '暂时无法连接服务，请确认后端正在运行后重试。',
  503: '服务或文件存储暂时不可用，请查询状态后再操作。',
  504: '服务响应超时，操作结果尚未确认。请先刷新状态，避免重复上传。',
};
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public location: string | null = null, public fields: string[] = []) { super(message); this.name = 'ApiError'; }
}

function errorFrom(status: number, payload: unknown, location: string | null): ApiError {
  let code = 'request_failed';
  let message = messages[status] || '操作未完成，请稍后重试。';
  let fields: string[] = [];
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail;
    // Only backend-authored Chinese messages are shown; old English detail and
    // Pydantic errors are translated without echoing untrusted submitted values.
    if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') {
      code = detail.code;
      if ('message' in detail && typeof detail.message === 'string' && /[\u3400-\u9fff]/.test(detail.message)) message = detail.message;
      if ('fields' in detail && Array.isArray(detail.fields)) fields = detail.fields.filter((x): x is string => typeof x === 'string');
    }
    if (typeof detail === 'string') {
      if (/no (extractable|selectable) text/i.test(detail)) message = 'PDF 中未发现可选中的文本，请导出文本型 PDF；扫描件暂不支持。';
      else if (/encrypted|password/i.test(detail)) message = '请先移除 PDF 的密码保护，再上传。';
      else if (/50 pages|page limit|too many pages/i.test(detail)) message = 'PDF 页数超过限制，请使用不超过 50 页的文件。';
      else if (/pdf/i.test(detail) && status === 400) message = 'PDF 无法读取，请确认文件完整、有文本且未加密。';
    }
  }
  return new ApiError(status, code, message, location && /^\/api\/v1\/resumes\/[0-9a-f-]+\/lifecycle$/i.test(location) ? location : null, fields);
}

export function formatError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.name === 'AbortError') return '操作已取消。';
  return '网络连接中断，请检查连接并刷新状态后重试。';
}

async function request<T>(path: string, init: RequestInit = {}, notifyUnauthorized = true): Promise<T> {
  const generation = sessionGeneration;
  const controller = new AbortController();
  const abort = () => controller.abort(init.signal?.reason);
  if (init.signal?.aborted) abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), 60_000);
  try {
    const response = await fetch(`/api/v1${path}`, { ...init, credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => null);
      if (response.status === 401 && notifyUnauthorized) {
        expireSession(generation);
      }
      throw errorFrom(response.status, payload, response.headers.get('Location'));
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (controller.signal.reason instanceof DOMException && controller.signal.reason.name === 'TimeoutError') {
      throw new ApiError(504, 'request_timeout', messages[504]);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener('abort', abort);
  }
}

async function getCsrf(): Promise<string> {
  if (csrfToken) return csrfToken;
  if (!csrfPending) {
    const generation = sessionGeneration;
    const controller = new AbortController();
    const pending = { controller, promise: Promise.resolve('') };
    pending.promise = request<Schemas['CsrfResponse']>('/auth/csrf', { signal: controller.signal }).then(data => {
      if (generation !== sessionGeneration || controller.signal.aborted) throw new DOMException('Session changed', 'AbortError');
      csrfToken = data.csrf_token;
      return data.csrf_token;
    }).finally(() => { if (csrfPending === pending) csrfPending = null; });
    csrfPending = pending;
  }
  return csrfPending.promise;
}
async function write<T>(path: string, method: string, data?: unknown, signal?: AbortSignal): Promise<T> {
  const token = await getCsrf();
  return request<T>(path, { method, signal, headers: { 'X-CSRF-Token': token, ...(data ? { 'Content-Type': 'application/json' } : {}) }, body: data ? JSON.stringify(data) : undefined });
}

export const api = {
  jobs: (query: URLSearchParams, signal?: AbortSignal) => request<JobList>(`/jobs?${query.toString()}`, { signal }),
  job: (id: string, signal?: AbortSignal) => request<Job>(`/jobs/${encodeURIComponent(id)}`, { signal }),
  parseJob: (jd_text: string, signal?: AbortSignal) => write<JobParsePreview>('/jobs/parse-preview', 'POST', { jd_text }, signal),
  createJob: (data: JobCreate, signal?: AbortSignal) => write<Job>('/jobs', 'POST', data, signal),
  updateJob: (id: string, data: JobUpdate, signal?: AbortSignal) => write<Job>(`/jobs/${encodeURIComponent(id)}`, 'PUT', data, signal),
  favoriteJob: (id: string, revision: number, is_favorite: boolean, signal?: AbortSignal) => write<Job>(`/jobs/${encodeURIComponent(id)}/favorite`, 'PATCH', { revision, is_favorite }, signal),
  deleteJob: (id: string, revision: number, signal?: AbortSignal) => write<void>(`/jobs/${encodeURIComponent(id)}`, 'DELETE', { revision }, signal),
  deepseekKey: (signal?: AbortSignal) => request<KeyStatus>('/ai/deepseek-key', { signal }),
  configureDeepseekKey: (api_key: string, signal?: AbortSignal) => write<KeyStatus>('/ai/deepseek-key', 'PUT', { api_key }, signal),
  revokeDeepseekKey: (signal?: AbortSignal) => write<KeyStatus>('/ai/deepseek-key', 'DELETE', undefined, signal),
  extractionPreview: (id: string, signal?: AbortSignal) => request<ExtractionPreview>(`/resumes/${encodeURIComponent(id)}/extraction-preview`, { signal }),
  me: (signal?: AbortSignal) => request<User>('/me', { signal }, false),
  csrf: async (signal?: AbortSignal) => {
    const generation = sessionGeneration;
    const data = await request<Schemas['CsrfResponse']>('/auth/csrf', { signal });
    if (generation === sessionGeneration) csrfToken = data.csrf_token;
    return data;
  },
  register: (data: Schemas['RegisterRequest'], signal?: AbortSignal) => request<User>('/auth/register', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }, false),
  login: async (data: Schemas['LoginRequest'], signal?: AbortSignal): Promise<LoginResponse> => {
    clearSession();
    const generation = sessionGeneration;
    const result = await request<LoginResponse>('/auth/login', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }, false);
    if (generation === sessionGeneration) csrfToken = result.csrf_token;
    return result;
  },
  logout: async (signal?: AbortSignal) => { await write<void>('/auth/logout', 'POST', undefined, signal); clearSession(); },
  profile: (signal?: AbortSignal) => request<CareerProfile>('/profile', { signal }),
  saveProfile: (data: ProfileSave, signal?: AbortSignal) => write<CareerProfile>('/profile', 'PUT', data, signal),
  reviewCandidate: (id: string, data: ProfileReview, signal?: AbortSignal) => write<CareerProfile>(`/profile/candidates/${encodeURIComponent(id)}/review`, 'POST', data, signal),
  extraction: (id: string, signal?: AbortSignal) => request<ExtractionCapability>(`/resumes/${encodeURIComponent(id)}/extraction`, { signal }),
  createExtraction: (id: string, data: ExtractionCreate, signal?: AbortSignal) => write<ExtractionRun>(`/resumes/${encodeURIComponent(id)}/extraction-runs`, 'POST', data, signal),
  extractionRun: (id: string, signal?: AbortSignal) => request<ExtractionRun>(`/extraction-runs/${encodeURIComponent(id)}`, { signal }),
  extractionCandidates: (id: string, signal?: AbortSignal) => request<ExtractionCandidates>(`/extraction-runs/${encodeURIComponent(id)}/candidates`, { signal }),
  cancelExtraction: (id: string, revision: number, signal?: AbortSignal) => write<ExtractionRun>(`/extraction-runs/${encodeURIComponent(id)}/cancel`, 'POST', { revision }, signal),
  retryExtraction: (id: string, revision: number, signal?: AbortSignal) => write<ExtractionRun>(`/extraction-runs/${encodeURIComponent(id)}/retry`, 'POST', { revision }, signal),
  resumes: (signal?: AbortSignal) => request<Resume[]>('/resumes', { signal }),
  resume: (id: string, signal?: AbortSignal) => request<Resume>(`/resumes/${encodeURIComponent(id)}`, { signal }),
  lifecycle: (id: string, signal?: AbortSignal) => request<ResumeLifecycle>(`/resumes/${encodeURIComponent(id)}/lifecycle`, { signal }),
  rename: (id: string, data: Schemas['ResumeRenameRequest'], signal?: AbortSignal) => write<Resume>(`/resumes/${encodeURIComponent(id)}`, 'PATCH', data, signal),
  remove: (id: string, signal?: AbortSignal) => write<void>(`/resumes/${encodeURIComponent(id)}`, 'DELETE', undefined, signal),
  async download(id: string, fileName: string, signal?: AbortSignal): Promise<void> {
    const generation = sessionGeneration;
    const response = await fetch(`/api/v1/resumes/${encodeURIComponent(id)}/download`, { signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000), credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) {
      if (response.status === 401) expireSession(generation);
      throw errorFrom(response.status, await response.json().catch(() => null), null);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = fileName; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  async upload(file: File, onProgress?: (percent: number) => void, signal?: AbortSignal): Promise<Resume> {
    const token = await getCsrf();
    const generation = sessionGeneration;
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const abort = () => xhr.abort();
      const cleanup = () => signal?.removeEventListener('abort', abort);
      xhr.open('POST', '/api/v1/resumes'); xhr.withCredentials = true; xhr.timeout = 60_000;
      xhr.setRequestHeader('X-CSRF-Token', token);
      xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100)); };
      xhr.onload = () => {
        cleanup();
        let payload: unknown;
        try { payload = JSON.parse(xhr.responseText); } catch { reject(new ApiError(502, 'upload_result_unknown', '上传结果尚未确认，请先刷新简历列表，避免重复上传。')); return; }
        if (xhr.status === 201) { resolve(payload as Resume); return; }
        if (xhr.status === 401) expireSession(generation);
        reject(errorFrom(xhr.status, payload, xhr.getResponseHeader('Location')));
      };
      xhr.onerror = () => { cleanup(); reject(new ApiError(0, 'network_error', '网络连接中断，上传结果尚未确认。请先刷新简历列表，避免重复上传。')); };
      xhr.ontimeout = () => { cleanup(); reject(new ApiError(504, 'request_timeout', messages[504])); };
      xhr.onabort = () => { cleanup(); reject(new ApiError(0, 'upload_result_unknown', '上传请求已中断，文件可能已保存。请先刷新简历列表，避免重复上传。')); };
      signal?.addEventListener('abort', abort, { once: true });
      const body = new FormData(); body.append('file', file); xhr.send(body);
    });
  },
};
