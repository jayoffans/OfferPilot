"use client";

import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import { api, ApiError, formatError, SESSION_RESET_EVENT, type Resume, type KeyStatus, type ExtractionRun, type ExtractionPreview } from "@/lib/api";
import { Notice, Spinner, formatDate } from "@/components/ui";

const statusNames = { queued: "等待处理", running: "正在解析", succeeded: "候选已生成", failed: "解析失败", cancelled: "已取消" };
const active = (run: ExtractionRun) => ["queued", "running"].includes(run.status);
const failures: Record<string, string> = {
  provider_unauthorized: "Key 无效，请撤销后重新配置。", provider_insufficient_balance: "DeepSeek 余额不足，请检查供应商账户。",
  provider_rate_limited: "供应商限流，请稍后再主动授权。", provider_invalid_request: "供应商拒绝参数，请联系项目负责人。",
  provider_timeout: "请求超时，可能已经收费，请先检查 DeepSeek 账单。", provider_connection_error: "网络中断，可能已经收费，请先检查 DeepSeek 账单。",
  billing_outcome_unknown: "调用结果未知，可能已经收费；不会自动重复调用，请先检查 DeepSeek 账单。",
  provider_server_error: "供应商异常，费用可能未知，请检查账单。", key_unavailable: "Key 已过期、撤销或服务重启，请重新配置。",
  session_invalid: "原会话已结束，任务停止。", schema_validation_failed: "返回内容未通过结构校验。未写入画像，可手工填写。",
  provider_truncated_response: "供应商输出被截断，未生成可信候选。", provider_empty_response: "供应商返回空内容，可手工填写。",
  provider_usage_exceeded_bounds: "供应商报告的 token 用量超出本次授权上限，结果已丢弃。请先核对 DeepSeek 账单并联系负责人；不会自动重试。",
  empty_extraction: "未抽取到候选事实，请核对发送范围或手工填写。", source_version_changed: "简历版本已变化，旧结果已丢弃。",
  resume_deleted: "来源简历已删除。", run_cancelled: "你已取消任务，已发送的请求仍可能收费。",
};

export function AiResumePanel({ onCandidates, disabled }: { onCandidates: () => void; disabled: boolean }) {
  const [key, setKey] = useState("");
  const [keyStatus, setKeyStatus] = useState<KeyStatus | null>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [selected, setSelected] = useState("");
  const [runs, setRuns] = useState<ExtractionRun[]>([]);
  const [preview, setPreview] = useState<ExtractionPreview | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [pending, setPending] = useState(false);
  const [reading, setReading] = useState(true);
  const [pollStopped, setPollStopped] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [dataRefresh, setDataRefresh] = useState(0);
  const action = useRef<AbortController | null>(null);
  const metadataRequest = useRef<AbortController | null>(null);
  const statusRequest = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const submitKey = useRef<string | null>(null);

  useEffect(() => {
    if (!keyStatus?.configured || !keyStatus.expires_at) return;
    const delay = Math.min(3600000, Math.max(0, new Date(keyStatus.expires_at).getTime() - Date.now()));
    const timer = setTimeout(() => { setKeyStatus(current => current ? { ...current, configured: false, expires_at: null } : current); setPreview(null); setAccepted(false); }, delay);
    return () => clearTimeout(timer);
  }, [keyStatus]);

  useEffect(() => {
    const reset = () => {
      metadataRequest.current?.abort(); statusRequest.current?.abort(); action.current?.abort();
      setKey(""); setPreview(null); setAccepted(false); setKeyStatus(null);
      setResumes([]); setSelected(""); setRuns([]); setPollStopped(false); setPending(false); setReading(false); submitKey.current = null;
    };
    window.addEventListener(SESSION_RESET_EVENT, reset);
    return () => { metadataRequest.current?.abort(); statusRequest.current?.abort(); action.current?.abort(); window.removeEventListener(SESSION_RESET_EVENT, reset); };
  }, []);

  useEffect(() => {
    const controller = new AbortController(); metadataRequest.current = controller;
    async function load() {
      try {
        const [status, files] = await Promise.all([api.deepseekKey(controller.signal), api.resumes(controller.signal)]);
        if (controller.signal.aborted) return;
        setKeyStatus(status); setResumes(files);
        if (!status.configured) { setPreview(null); setAccepted(false); }
      } catch (cause) { if (!controller.signal.aborted) setError(formatError(cause)); }
      finally { if (!controller.signal.aborted) setReading(false); }
    }
    void load();
    return () => controller.abort();
  }, [dataRefresh]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); statusRequest.current = controller;
    async function poll() {
      try {
        for (let count = 0; count < 70 && !controller.signal.aborted; count++) {
          const data = await api.extraction(selected, controller.signal);
          if (controller.signal.aborted) return;
          const records = data.recent_runs.filter(run => run.mode === "deepseek"); setRuns(records);
          if (!records.some(active)) return;
          if (count === 69) { setPollStopped(true); return; }
          await new Promise<void>(resolve => {
            const abort = () => { clearTimeout(timer); resolve(); };
            const timer = setTimeout(() => { controller.signal.removeEventListener("abort", abort); resolve(); }, 1000);
            controller.signal.addEventListener("abort", abort, { once: true });
            if (controller.signal.aborted) abort();
          });
        }
      } catch (cause) { if (!controller.signal.aborted) setError(formatError(cause)); }
    }
    void poll(); return () => controller.abort();
  }, [selected, refresh]);

  async function perform(operation: (signal: AbortSignal) => Promise<void>) {
    if (busy.current) return;
    busy.current = true; setPending(true); setError(null);
    const controller = new AbortController(); action.current = controller;
    try { await operation(controller.signal); }
    catch (cause) {
      if (controller.signal.aborted) return;
      setError(`${formatError(cause)} 操作不会自动重试；请先刷新任务状态。`);
      if (cause instanceof ApiError && cause.code === "key_unavailable") {
        setKeyStatus(current => current ? { ...current, configured: false, expires_at: null } : current);
        setPreview(null); setAccepted(false);
        try { const status = await api.deepseekKey(controller.signal); if (!controller.signal.aborted) setKeyStatus(status); }
        catch { /* Keep the safe unavailable state until the user refreshes. */ }
      }
    }
    finally { busy.current = false; if (!controller.signal.aborted) setPending(false); }
  }

  function configure() {
    const supplied = key; setKey(""); setPreview(null); setAccepted(false);
    void perform(async signal => { const data = await api.configureDeepseekKey(supplied, signal); if (!signal.aborted) setKeyStatus(data); });
  }
  function refreshStatus() {
    if (busy.current || reading) return;
    setError(null); setReading(true); setPollStopped(false); setDataRefresh(value => value + 1); setRefresh(value => value + 1);
  }
  function authorize() {
    if (!preview || !accepted) return;
    const snapshot = preview; submitKey.current ??= crypto.randomUUID();
    void perform(async signal => {
      const result = await api.createExtraction(selected, { mode: "deepseek", idempotency_key: submitKey.current!, source_version: snapshot.source_version,
        authorization: { authorization_id: snapshot.authorization_id, accepted: true, max_calls: 1 } }, signal);
      if (signal.aborted) return;
      setRuns(current => [result, ...current.filter(run => run.id !== result.id)]); setPreview(null); setAccepted(false); setPollStopped(false); submitKey.current = null; setRefresh(value => value + 1);
    });
  }

  return <section className="panel ai-resume-panel" aria-labelledby="ai-resume-heading"><div className="section-heading"><div><h2 id="ai-resume-heading">AI 简历解析</h2><p>DeepSeek · 先授权，再解析，逐项审核后写入资料。</p></div><span className="badge badge-neutral">用户自备 Key</span></div>
    {error && !preview && <Notice>{error}</Notice>}
    <p>Key 仅用于本次登录会话，后端内存限时保存，最长 {Math.round((keyStatus?.ttl_seconds || 3600) / 60)} 分钟。退出、撤销、过期或服务重启后需重新输入。配置 Key 本身不会调用模型。</p>
    {keyStatus && !keyStatus.enabled && <Notice tone="info">{keyStatus.unavailable_reason}</Notice>}
    <form onSubmit={event => { event.preventDefault(); configure(); }} className="ai-key-form"><label htmlFor="deepseek-key">DeepSeek API Key</label><input id="deepseek-key" type="password" autoComplete="off" spellCheck={false} value={key} onChange={event => setKey(event.target.value)} disabled={pending || reading || !keyStatus?.enabled} placeholder="输入自己的 Key" /><button className="button button-secondary" disabled={pending || reading || !key.trim() || !keyStatus?.enabled}>配置会话 Key</button></form>
    <div className="candidate-actions"><span>{keyStatus?.configured ? `当前会话已配置 · 到期 ${formatDate(keyStatus.expires_at!, true)}` : "当前会话未配置 Key"}</span>{keyStatus?.configured && <button className="text-button" disabled={pending || reading} onClick={() => { setKey(""); setPreview(null); void perform(async signal => { const data = await api.revokeDeepseekKey(signal); if (!signal.aborted) { setKeyStatus(data); setPollStopped(false); setRefresh(value => value + 1); } }); }}>撤销 Key</button>}</div>
    <label htmlFor="ai-resume-select">选择已上传的简历</label><select id="ai-resume-select" value={selected} disabled={pending} onChange={event => { setSelected(event.target.value); setRuns([]); setPreview(null); setAccepted(false); setPollStopped(false); }}><option value="">请选择简历</option>{resumes.map(file => <option key={file.id} value={file.id}>{file.file_name}</option>)}</select>
    {!resumes.length && <p>请先<Link className="inline-link" href="/resumes">上传文本 PDF</Link>，或继续手工填写资料。</p>}
    <div className="candidate-actions"><button className="button button-primary" disabled={pending || reading || !selected || !keyStatus?.enabled || !keyStatus.configured || runs.some(active)} onClick={() => { void perform(async signal => { const data = await api.extractionPreview(selected, signal); if (!signal.aborted) { setPreview(data); setAccepted(false); submitKey.current = null; } }); }}>预览发送内容与费用</button><button className="button button-secondary" disabled={pending || reading} onClick={refreshStatus}>刷新任务状态</button>{pending && <Spinner label="正在处理操作" />}{reading && <Spinner label="正在读取 Key 与简历状态" />}</div>
    <p className="field-hint">上传、浏览和刷新只读取状态。每次真实解析最多 1 次调用，不自动修复或重试。</p>
    {pollStopped && <Notice tone="info">自动查询已达到本次上限，页面显示最后一次读取的状态。任务仍保存在服务器；请稍后点击“刷新任务状态”。刷新不会重新调用模型。</Notice>}
    {runs.map(run => <article className="profile-entry" key={run.id}><div className="profile-entry-heading"><strong>DeepSeek · {run.model}</strong><span className="badge badge-neutral">{statusNames[run.status]}</span></div><p>运行记录 {run.id} · 调用 {run.call_count}/{run.max_calls}</p>{run.total_tokens !== null && run.total_tokens !== undefined && <p>供应商报告 token：输入 {run.prompt_tokens}，输出 {run.completion_tokens}，合计 {run.total_tokens}。实际费用以供应商账单为准。</p>}{run.billing_status === "unknown" && <p>费用状态未知，已发请求可能收费。</p>}{run.error_code && <Notice>{failures[run.error_code] || "解析未完成，可手工填写资料或联系负责人检查。"}</Notice>}{active(run) && <button className="button button-secondary" disabled={pending} onClick={() => { void perform(async signal => { await api.cancelExtraction(run.id, run.revision, signal); if (!signal.aborted) { setPollStopped(false); setRefresh(value => value + 1); } }); }}>取消任务（已发请求仍可能收费）</button>}{run.status === "succeeded" && <><p>{run.candidate_count} 项 AI 待审核内容已保存，尚未写入正式资料。</p><button className="button button-secondary" disabled={disabled} onClick={onCandidates}>载入待审核候选</button></>}</article>)}
    <Dialog.Root open={!!preview} onOpenChange={open => { if (!open && !pending) { setPreview(null); setAccepted(false); } }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content ai-consent-dialog"><Dialog.Title>授权本次 AI 解析</Dialog.Title><Dialog.Description>使用 {preview?.provider} / {preview?.model} 非思考模式。请核对发送范围和费用后主动确认。</Dialog.Description>{preview && <>{error && <Notice>{error}</Notice>}<p>{preview.privacy_notice}</p><pre className="ai-preview-text" aria-label="将发送的脱敏简历文本">{preview.redacted_text}</pre><p>输入 token 保守上界 {preview.max_input_tokens}，输出上限 {preview.max_output_tokens}，最多 {preview.max_calls} 次调用。授权预览十分钟内有效。</p><Notice tone="info">{preview.cost_notice}</Notice><p><a className="inline-link" href="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/" target="_blank" rel="noreferrer">官方实时价格</a> · <a className="inline-link" href="https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html" target="_blank" rel="noreferrer">供应商隐私政策</a></p><label className="ai-consent-check"><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)} disabled={pending} />我已检查脱敏内容，同意发送给 DeepSeek，并使用自己的 Key 承担本次调用费用。</label><div className="dialog-actions"><button className="button button-secondary" disabled={pending} onClick={() => { setPreview(null); setAccepted(false); }}>暂不解析</button><button className="button button-primary" disabled={!accepted || pending} onClick={authorize}>开始 AI 解析</button></div></>}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
