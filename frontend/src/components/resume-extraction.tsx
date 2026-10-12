"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { FlaskConical, RefreshCw } from "lucide-react";
import { api, ApiError, formatError, type ExtractionCapability, type ExtractionRun } from "@/lib/api";
import { formatDate, Notice, Spinner } from "@/components/ui";

const statusNames: Record<ExtractionRun["status"], string> = { queued: "等待处理", running: "正在处理", succeeded: "候选已生成", failed: "抽取失败", cancelled: "已取消" };
const active = (run: ExtractionRun) => run.status === "queued" || run.status === "running";

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export function ResumeExtraction({ id, canRun, enabled }: { id: string; canRun: boolean; enabled: boolean }) {
  const [capability, setCapability] = useState<ExtractionCapability | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [refreshCount, setRefreshCount] = useState(0);
  const busy = useRef(false);
  const actionController = useRef<AbortController | null>(null);
  // Preserve this key when a create response is lost, until lookup confirms it.
  const createKey = useRef<string | null>(null);
  const createVersion = useRef<string | null>(null);

  useEffect(() => () => actionController.current?.abort(), []);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    async function load() {
      let polls = 0;
      try {
        while (!controller.signal.aborted) {
          const data = await api.extraction(id, controller.signal);
          if (controller.signal.aborted) return;
          setCapability(data); setLoading(false); setError(null);
          if (!data.recent_runs.some(active)) return;
          if (++polls >= 20) { setPollStopped(true); return; }
          await pause(controller.signal);
        }
      } catch (cause) { if (!controller.signal.aborted) { setError(formatError(cause)); setLoading(false); } }
    }
    void load();
    return () => controller.abort();
  }, [id, refreshCount, enabled]);

  function refresh() { if (!busy.current) { setPollStopped(false); setRefreshCount(value => value + 1); } }
  async function act(action: "create" | "cancel" | "retry", run?: ExtractionRun) {
    if (busy.current || !capability?.enabled || !canRun) return;
    busy.current = true; setPending(run?.id || "create"); setError(null);
    const controller = new AbortController(); actionController.current = controller;
    try {
      if (action === "create") {
        if (createVersion.current !== capability.source_version) createKey.current = null;
        createVersion.current = capability.source_version;
        createKey.current ??= crypto.randomUUID();
        await api.createExtraction(id, { idempotency_key: createKey.current, source_version: capability.source_version, mode: "mock" }, controller.signal);
        createKey.current = null;
      } else if (run) {
        if (action === "cancel") await api.cancelExtraction(run.id, run.revision, controller.signal);
        else await api.retryExtraction(run.id, run.revision, controller.signal);
      }
      if (!controller.signal.aborted) { setPollStopped(false); setRefreshCount(value => value + 1); }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof ApiError && cause.status === 409 ? "任务状态或简历版本已改变，请刷新任务状态后再操作。" : `${formatError(cause)} 请先刷新任务状态，确认服务器结果。`);
      }
    } finally { busy.current = false; if (!controller.signal.aborted) setPending(null); }
  }

  if (!enabled) return null;
  const existingVersion = capability?.recent_runs.some(run => run.source_version === capability.source_version);
  return <section className="panel lifecycle-panel extraction-panel" aria-labelledby="extraction-test-heading"><div className="section-heading"><div><h2 id="extraction-test-heading"><FlaskConical size={18} aria-hidden="true" /> Mock 简历抽取测试</h2><p>隔离测试模式 · 使用合成资料验证任务与审核流程。</p></div><span className="badge badge-warning">测试结果</span></div><Notice tone="info">Mock 输出用于测试，不是真实 AI 分析。候选不会自动成为正式资料；请到“我的资料”逐项审核。真实 DeepSeek 尚未启用。</Notice>{error && <Notice>{error}</Notice>}{loading ? <Spinner label="正在读取抽取任务" /> : <><div className="file-overview-actions"><button className="button button-secondary" disabled={!capability?.enabled || !canRun || !!pending || existingVersion} onClick={() => { void act("create"); }}>{pending === "create" ? <Spinner label="正在创建测试任务" /> : "创建 Mock 测试任务"}</button><button className="button button-secondary" disabled={!!pending} onClick={refresh}><RefreshCw size={15} />刷新任务状态</button></div>{!capability?.enabled && <p className="field-hint">后端 Mock 测试模式未启用，当前无法创建测试任务。</p>}{!canRun && <p className="field-hint">简历须完成文本解析且仍可用，才能创建抽取任务。</p>}{!capability?.recent_runs.length ? <p className="state-unknown">尚无测试任务，上传本身不会生成候选。</p> : capability.recent_runs.map(run => <article className="profile-entry" key={run.id} data-extraction-run={run.id}><div className="profile-entry-heading"><strong>Mock 测试任务</strong><span className={`badge ${run.status === "succeeded" ? "badge-success" : run.status === "failed" ? "badge-warning" : "badge-neutral"}`}>{statusNames[run.status]}</span></div><p>创建于 {formatDate(run.created_at, true)} · 尝试 {run.attempt_count}/{run.max_attempts} · 调用 {run.call_count}/{run.max_calls}</p>{run.status === "succeeded" && <p>{run.candidate_count} 项测试候选已保存，仍待用户审核。<Link className="inline-link" href="/profile">查看并审核候选</Link></p>}{run.error_code && <p className="field-hint">安全错误代码：<code>{run.error_code}</code></p>}<div className="candidate-actions">{active(run) && <button className="button button-secondary" disabled={!!pending || !canRun} onClick={() => { void act("cancel", run); }}>{pending === run.id ? <Spinner label="正在取消" /> : "取消测试任务"}</button>}{run.status === "failed" && run.attempt_count < run.max_attempts && run.call_count < run.max_calls && <button className="button button-secondary" disabled={!!pending || !canRun} onClick={() => { void act("retry", run); }}>{pending === run.id ? <Spinner label="正在重试" /> : "有限重试测试任务"}</button>}</div></article>)}{pollStopped && <Notice tone="info">自动查询已达到本次上限。任务状态已保存，请稍后点击“刷新任务状态”。</Notice>}</>}</section>;
}
