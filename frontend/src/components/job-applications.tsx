"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowUpRight, ClipboardList, Plus } from "lucide-react";
import { api, ApiError, type ApplicationList, type Job, type JobMatch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ApplicationBadge, applicationError, isTerminal } from "@/components/applications-ui";
import { formatDate, Notice, Spinner } from "@/components/ui";

export function JobApplicationsPanel({ job, disabled = false }: { job: Job; disabled?: boolean }) {
  const { user, status } = useAuth();
  const router = useRouter();
  const [records, setRecords] = useState<ApplicationList | null>(null);
  const [match, setMatch] = useState<JobMatch | null>(null);
  const [includeMatch, setIncludeMatch] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [reapplyOpen, setReapplyOpen] = useState(false);
  const [owner, setOwner] = useState<string | null>(null);
  const attempt = useRef<{ serialized: string; key: string } | null>(null);
  const pending = useRef(false);
  const action = useRef<AbortController | null>(null);
  const userId = user?.id;
  useEffect(() => () => { action.current?.abort(); attempt.current = null; pending.current = false; }, [userId, status]);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
    setLoading(true); setError(null);
    const query = new URLSearchParams({ job_id: job.id, sort: "created_desc", page: "1", page_size: "100" });
    void api.applications(query, controller.signal).then(data => { if (!controller.signal.aborted) { setRecords(data); setOwner(userId); } }).catch(cause => { if (!controller.signal.aborted) setError(applicationError(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    void api.latestMatch(job.id, controller.signal).then(data => { if (!controller.signal.aborted) setMatch(data); }).catch(() => { if (!controller.signal.aborted) setMatch(null); });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [job.id, job.revision, userId, status, refresh]);
  const visible = owner === userId ? records : null;
  const active = visible?.items.find(item => !isTerminal(item.status));
  const previous = visible?.items[0];
  async function create(reapply: boolean) {
    if (pending.current || disabled || !visible || active) return;
    setReapplyOpen(false); pending.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); action.current = controller;
    const data = { job_id: job.id, job_revision: job.revision, reapply, match_id: includeMatch && match ? match.id : null };
    const serialized = JSON.stringify(data);
    if (attempt.current?.serialized !== serialized) attempt.current = { serialized, key: crypto.randomUUID() };
    try {
      const result = await api.createApplication({ ...data, idempotency_key: attempt.current.key }, controller.signal);
      if (!controller.signal.aborted) { attempt.current = null; router.push(`/applications/${result.id}`); }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof ApiError && cause.code === "application_already_exists") { setRefresh(value => value + 1); setError("这个职位已有申请记录。请打开已有记录；结束后可明确创建新一轮申请。"); }
        else setError(`${applicationError(cause)} 如结果未知，请查询已有记录；相同内容重试会复用本次请求。`);
      }
    } finally { pending.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="panel job-applications" aria-labelledby="job-applications-title"><div className="section-heading"><div><h2 id="job-applications-title"><ClipboardList size={19} />投递记录</h2><p>由你决定何时开始、如何推进。收藏和匹配结论不会自动改变申请状态。</p></div><Link className="inline-link" href="/applications">全部申请<ArrowUpRight size={14} /></Link></div>
    {loading ? <Spinner label="正在查询这个职位的申请" /> : <>{error && <Notice>{error}</Notice>}{!visible ? <button className="button button-secondary" onClick={() => setRefresh(value => value + 1)}>重新查询申请</button> : <>{active ? <div className="application-current"><div><ApplicationBadge status={active.status} /><span>第 {active.attempt_number} 次申请 · {formatDate(active.updated_at, true)} 更新</span></div><Link className="button button-primary" href={`/applications/${active.id}`}>继续跟踪申请<ArrowUpRight size={16} /></Link></div> : <div className="application-start"><div><strong>{previous ? "重新考虑这个机会？" : "决定把这个机会列入计划？"}</strong><p>{previous ? "上一轮已结束。明确创建新一轮，保留此前的独立历史。" : "先建立“准备投递”记录，实际投递后再由你更新。无需重复填写岗位信息。"}</p>{match && <label className="application-match-check"><input type="checkbox" checked={includeMatch} disabled={busy || disabled} onChange={event => setIncludeMatch(event.target.checked)} />关联这份匹配分析（{match.stale ? "历史结果，依据已变更" : "当前依据"}）</label>}</div><button className="button button-primary" disabled={busy || disabled} onClick={() => { if (previous) setReapplyOpen(true); else void create(false); }}>{busy ? <Spinner label="正在创建" /> : <><Plus size={16} />{previous ? "创建新一轮申请" : "准备投递"}</>}</button></div>}{visible.items.length > 0 && <details className="application-job-history"><summary>查看该职位的申请记录（{visible.total} 条）</summary>{visible.items.map(item => <Link className="application-history-link" key={item.id} href={`/applications/${item.id}`}><span>第 {item.attempt_number} 次 · {formatDate(item.created_at)}</span><ApplicationBadge status={item.status} /><ArrowUpRight size={14} /></Link>)}</details>}<p className="field-hint">存在申请记录时，删除职位会被阻止，以保护申请历史。申请详情可单独确认删除记录。</p></>}</>}
    <Dialog.Root open={reapplyOpen} onOpenChange={setReapplyOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content compact-dialog"><Dialog.Title>为这个职位创建新一轮申请？</Dialog.Title><Dialog.Description className="delete-description">新一轮从“准备投递”开始。上一轮记录和时间线会保留，实际是否允许再次投递需由你向招聘方核实。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary">保留原有记录</Dialog.Close><button className="button button-primary" onClick={() => { void create(true); }}>确认创建新一轮</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
