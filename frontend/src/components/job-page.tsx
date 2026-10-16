"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, ArrowRight, ArrowUpRight, BriefcaseBusiness, Check, FileText, Pencil, RefreshCw, Save, Star, Trash2 } from "lucide-react";
import { api, ApiError, type Job, type JobRequirementInput } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { forgetJobDraft, readJobDraft, rememberJobDraft } from "@/lib/job-drafts";
import { WorkspaceShell } from "@/components/workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { blankJob, JobBasicFields, jobDraft, JobJdField, requirementInput, RequirementEditor, validateJob, type JobDraft } from "@/components/job-editor";
import { JobMatchPanel } from "@/components/job-match";
import { JobApplicationsPanel } from "@/components/job-applications";
import { jobError, priorities, requirementTypes, reviewLabel, safeJobLink, workTypes } from "@/components/jobs-ui";

export function JobPage({ id }: { id?: string }) {
  const { user, status } = useAuth();
  const userId = user?.id;
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [draft, setDraft] = useState<JobDraft>({ ...blankJob });
  const [owner, setOwner] = useState<string | null>(null);
  const [baseRevision, setBaseRevision] = useState<number | null>(null);
  const [step, setStep] = useState(1);
  const [editing, setEditing] = useState(!id);
  const [loading, setLoading] = useState(!!id);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<Job | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [reloadCount, setReloadCount] = useState(0);
  const [matchRefresh, setMatchRefresh] = useState(0);
  const [restored, setRestored] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [parseOpen, setParseOpen] = useState(false);
  const action = useRef<AbortController | null>(null);
  const actionBusy = useRef(false);
  const createAttempt = useRef<{ serialized: string; key: string } | null>(null);
  const baseline = job ? jobDraft(job) : blankJob;
  const dirty = owner === userId && JSON.stringify(draft) !== JSON.stringify(baseline);
  const currentJdVersion = job ? job.jd_version + (draft.jd_text !== job.jd_text ? 1 : 0) : 1;

  useEffect(() => () => { action.current?.abort(); actionBusy.current = false; }, [userId, status]);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const data = id ? await api.job(id, controller.signal) : null;
        if (controller.signal.aborted) return;
        const cached = readJobDraft(userId!, id);
        const changed = !!cached && !!data && cached.revision !== data.revision;
        setJob(data); setOwner(userId!); setDraft(cached?.draft || (data ? jobDraft(data) : { ...blankJob })); setBaseRevision(cached?.revision ?? data?.revision ?? null); setStep(cached?.step || 1); setEditing(!id || !!cached); setConflict(changed); setLatest(changed ? data : null); setRestored(!!cached); setLoadError(null);
        setError(changed ? "职位已在其他页面更新。本次编辑已保留，请核对最新内容后重新载入，当前禁止覆盖保存。" : null);
      } catch (cause) { if (!controller.signal.aborted) { setLoadError(jobError(cause)); setOwner(userId!); } }
      finally { if (!controller.signal.aborted) { setLoading(false); setBusy(false); } }
    }
    void load();
    return () => controller.abort();
  }, [status, userId, id, reloadCount]);
  useEffect(() => {
    if (!userId || owner !== userId || status !== "authenticated") return;
    if (dirty) rememberJobDraft(userId, id, { draft, revision: baseRevision, step });
    else forgetJobDraft(userId, id);
  }, [userId, owner, status, dirty, id, draft, baseRevision, step]);

  function apply(data: Job) { if (userId) forgetJobDraft(userId, id); setJob(data); setDraft(jobDraft(data)); setBaseRevision(data.revision); setConflict(false); setLatest(null); setRestored(false); setWarnings([]); }
  function discard() { if (userId) forgetJobDraft(userId, id); setDraft(job ? jobDraft(job) : { ...blankJob }); setBaseRevision(job?.revision ?? null); setRestored(false); }
  function failure(cause: unknown) { if (cause instanceof ApiError && cause.code === "job_has_applications") { setError(cause.message); return; } if (cause instanceof ApiError && cause.status === 409) { setConflict(true); setError("职位版本发生冲突。本次编辑已保留，尚未覆盖服务器内容。请查看最新保存内容后重新载入。"); } else setError(jobError(cause)); }
  function change(key: keyof JobDraft, value: string | null) { setDraft(current => ({ ...current, [key]: value })); setSuccess(null); if (!conflict) setError(null); }
  function changeJd(jd_text: string) { setDraft(current => ({ ...current, jd_text, requirements: current.requirements.map(item => ({ ...item, review_status: "pending" })) })); setWarnings([]); setSuccess(null); if (!conflict) setError(null); }
  function requirements(items: JobRequirementInput[]) { setDraft(current => ({ ...current, requirements: items })); setSuccess(null); if (!conflict) setError(null); }
  function beginAction() { actionBusy.current = true; setBusy(true); setError(null); setSuccess(null); const controller = new AbortController(); action.current = controller; return controller; }
  function endAction(controller: AbortController) { actionBusy.current = false; if (!controller.signal.aborted) setBusy(false); }
  async function parse() {
    setParseOpen(false);
    if (actionBusy.current || !draft.jd_text?.trim() || conflict) return;
    const controller = beginAction();
    try { const result = await api.parseJob(draft.jd_text, controller.signal); if (!controller.signal.aborted) { requirements(result.requirements.map(item => ({ ...requirementInput(item), source_jd_version: currentJdVersion, review_status: "pending" }))); setWarnings(result.warnings); setSuccess("已按免费确定性规则整理，全部为待审核条件。请对照原文核对，规则可能遗漏或误分。未调用大模型。"); } }
    catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { endAction(controller); }
  }
  async function save() {
    if (actionBusy.current || conflict) return;
    const issue = validateJob(draft); if (issue) { setError(issue); return; }
    const controller = beginAction();
    try {
      let result: Job;
      if (job) result = await api.updateJob(job.id, { ...draft, revision: baseRevision ?? job.revision }, controller.signal);
      else {
        const serialized = JSON.stringify(draft);
        if (createAttempt.current?.serialized !== serialized) createAttempt.current = { serialized, key: crypto.randomUUID() };
        result = await api.createJob({ ...draft, idempotency_key: createAttempt.current.key }, controller.signal);
      }
      if (!controller.signal.aborted) { apply(result); setEditing(false); setSuccess("职位已保存。待审核条件会保留审核进度，刷新或重新登录后仍可继续。未审核条件不会作为可信匹配依据。"); if (!id) router.replace(`/jobs/${result.id}`); }
    } catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { endAction(controller); }
  }
  async function favorite() {
    if (!job || actionBusy.current || dirty || conflict) return;
    const controller = beginAction();
    try { const result = await api.favoriteJob(job.id, job.revision, !job.is_favorite, controller.signal); if (!controller.signal.aborted) { apply(result); setSuccess(result.is_favorite ? "职位已收藏。" : "已取消收藏。"); } }
    catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { endAction(controller); }
  }
  async function remove() {
    if (!job || actionBusy.current || conflict) return;
    const controller = beginAction();
    try { await api.deleteJob(job.id, job.revision, controller.signal); if (!controller.signal.aborted) { if (userId) forgetJobDraft(userId, id); setDeleteOpen(false); setDraft(jobDraft(job)); router.replace("/jobs"); } }
    catch (cause) { if (!controller.signal.aborted) { setDeleteOpen(false); failure(cause); } }
    finally { endAction(controller); }
  }
  async function readLatest() {
    if (!job || actionBusy.current) return;
    const controller = beginAction();
    try { const result = await api.job(job.id, controller.signal); if (!controller.signal.aborted) setLatest(result); }
    catch (cause) { if (!controller.signal.aborted) setError(`最新内容暂时无法读取。${jobError(cause)} 本次编辑仍保留。`); }
    finally { endAction(controller); }
  }
  function reload() { if (actionBusy.current) return; if (dirty || conflict) setResetOpen(true); else { setLoading(true); setReloadCount(value => value + 1); } }
  function reset() { discard(); setResetOpen(false); setLoading(true); setSuccess(null); setReloadCount(value => value + 1); }
  const showEditor = !job || editing;
  const link = safeJobLink(job?.source_url);
  return <WorkspaceShell hasUnsavedChanges={dirty} onDiscard={discard}>
    <Link href="/jobs" className="back-link"><ArrowLeft size={15} />返回职位列表</Link>
    <PageHeading eyebrow={job ? "原文有据，条件可复核" : "从招聘原文开始"} title={job ? "职位详情" : "添加职位"} description={job ? "查看招聘信息与审核进度，修正条件后再作下一步判断。" : "先粘贴 JD，填写必要信息，再逐项核对条件。"} action={job && <button className="button button-secondary" disabled={busy || loading} onClick={reload}><RefreshCw size={16} />重新载入</button>} />
    {loading || owner !== userId ? <div className="loading-card"><Spinner label="正在读取职位" /></div> : loadError ? <section className="panel job-panel-body"><h2>职位暂时无法读取</h2><Notice>{loadError}</Notice><button className="button button-secondary" onClick={reload}>重试</button></section> : <>
      {restored && <Notice tone="info">恢复了本标签页尚未保存的编辑。关闭页面或退出登录会清除临时编辑，请及时保存。</Notice>}
      {success && <Notice tone="success">{success}</Notice>}{error && <Notice>{error}</Notice>}
      {conflict && <section className="profile-conflict"><div className="profile-conflict-actions"><button className="button button-secondary" disabled={busy} onClick={() => { void readLatest(); }}>查看最新保存内容</button><button className="text-button" disabled={busy} onClick={() => setResetOpen(true)}>放弃本次修改，重新载入</button></div>{latest && <details className="profile-latest" open><summary>服务器最新职位（只读）</summary><p>岗位名称：{latest.title}</p><p>公司：{latest.company || "未知"} · 地点：{latest.location || "未知"} · 版本 {latest.revision}</p><p>{reviewLabel(latest.review_summary)}</p><pre className="job-jd-text">{latest.jd_text || "暂无 JD"}</pre></details>}</section>}
      {job && <section className="panel job-overview"><div className="job-overview-heading"><span className="job-icon job-icon-large"><BriefcaseBusiness size={27} /></span><div><h2>{job.title}</h2><p>{job.company || "公司待补充"} · {job.location || "地点未知"}</p><div className="job-row-tags"><span>{workTypes[job.work_type]}</span><span>{job.salary || "薪资未知"}</span><span className="badge badge-neutral">{reviewLabel(job.review_summary)}</span></div></div></div><div className="job-overview-actions"><button className="button button-secondary" disabled={busy || dirty || conflict} onClick={() => { void favorite(); }}><Star size={16} fill={job.is_favorite ? "currentColor" : "none"} />{job.is_favorite ? "取消收藏" : "收藏职位"}</button><button className="button button-secondary" disabled={busy} onClick={() => setEditing(true)}><Pencil size={16} />编辑职位</button><button className="text-button danger-text" disabled={busy || dirty || conflict} onClick={() => setDeleteOpen(true)}><Trash2 size={16} />删除职位</button></div><div className="job-provenance"><span>来源：{job.source_kind === "pasted" ? "用户粘贴 JD" : "用户手工录入"}</span><span>创建：{formatDate(job.created_at, true)}</span><span>更新：{formatDate(job.updated_at, true)}</span><span>内容版本 {job.revision} · JD v{job.jd_version}</span></div>{link && <a href={link} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="inline-link job-source-link">查看原始招聘链接<ArrowUpRight size={14} /><span>{link}</span></a>}</section>}
      {job && !showEditor && <><div id="job-match"><JobMatchPanel key={`${userId}:${job.id}`} job={job} disabled={busy || dirty || conflict} onReview={() => setEditing(true)} onMatchSaved={() => setMatchRefresh(value => value + 1)} /></div><JobApplicationsPanel key={`applications:${userId}:${job.id}`} job={job} disabled={busy || dirty || conflict} matchRefresh={matchRefresh} /></>}
      {showEditor ? <form onSubmit={event => { event.preventDefault(); void save(); }}>
        {!job && <ol className="job-import-steps" aria-label="导入步骤">{["粘贴 JD", "基本信息", "审核并保存"].map((label, index) => <li key={label} className={step === index + 1 ? "active" : step > index + 1 ? "complete" : ""}><span>{step > index + 1 ? <Check size={14} /> : index + 1}</span>{label}</li>)}</ol>}
        {(!job && step === 1) ? <section className="panel job-panel-body"><h2>粘贴招聘信息</h2><p className="job-section-intro">保留完整原文，方便后续对照。没有 JD 时也可以手工创建。</p><JobJdField value={draft.jd_text || ""} disabled={busy} onChange={changeJd} /><div className="job-step-actions"><button type="button" className="text-button" disabled={busy} onClick={() => setStep(2)}>手工录入</button><button type="button" className="button button-primary" disabled={busy || !draft.jd_text?.trim()} onClick={() => setStep(2)}>继续填写基本信息<ArrowRight size={16} /></button></div></section> : <>
          {(job || step === 2) && <section className="panel job-panel-body job-basic-panel"><h2>职位基本信息</h2><p className="job-section-intro">只需岗位名称即可保存。其余信息按招聘原文填写，未明确的内容留空。</p><JobBasicFields draft={draft} disabled={busy} onChange={change} />{job && <><JobJdField value={draft.jd_text || ""} disabled={busy} onChange={changeJd} />{draft.jd_text !== job.jd_text && <p className="job-review-warning">先保存修改后的 JD，所有条件将回到待审核。保存后再对照新原文绑定并逐项确认；当前仍可修订条件并保存进度。</p>}</>}{!job && <div className="job-step-actions"><button type="button" className="text-button" onClick={() => setStep(1)}>上一步</button><button type="button" className="button button-primary" disabled={!draft.title.trim()} onClick={() => { const issue = validateJob(draft); if (issue) setError(issue); else { setError(null); setStep(3); } }}>继续审核条件<ArrowRight size={16} /></button></div>}</section>}
          {(job || step === 3) && <><section className="panel job-rule-panel"><div><span className="job-rule-icon"><FileText size={21} /></span><div><h3>按原文整理候选条件</h3><p>免费确定性规则，不调用大模型。仅覆盖部分表达，全部结果待人工审核。</p></div></div><button type="button" className="button button-secondary" disabled={busy || conflict || !draft.jd_text?.trim()} onClick={() => { if (draft.requirements.length) setParseOpen(true); else void parse(); }}>{busy ? <Spinner label="正在处理" /> : "免费规则整理"}</button></section>{warnings.map((warning, index) => <Notice key={index} tone="info">{warning}</Notice>)}<div className="job-review-layout"><div><RequirementEditor items={draft.requirements} job={job} jdText={draft.jd_text || ""} jdVersion={currentJdVersion} disabled={busy} reviewBlocked={!!job && draft.jd_text !== job.jd_text} onChange={requirements} /></div><aside className="panel job-original"><div className="section-heading"><div><h2>招聘原文</h2><p>JD v{currentJdVersion} · 对照每一条条件</p></div></div><pre className="job-jd-text">{draft.jd_text || "未提供 JD。可以手动补充条件，请将未明确内容标为待核实。"}</pre></aside></div><div className="profile-savebar job-savebar"><div><strong>{job ? dirty ? "有修改尚未保存" : "已与服务器同步" : "可以保存当前审核进度"}</strong><p>{draft.requirements.filter(item => item.review_status !== "confirmed" || (item.source_jd_version != null && item.source_jd_version !== currentJdVersion)).length} 项待审核 · 未审核条件不会成为可信匹配依据。</p></div><div>{!job && <button type="button" className="text-button" disabled={busy} onClick={() => setStep(2)}>修改基本信息</button>}{job && !dirty && <button type="button" className="text-button" onClick={() => setEditing(false)}>结束编辑</button>}<button className="button button-primary" type="submit" disabled={busy || conflict || (!!job && !dirty)}>{busy ? <Spinner label="正在保存" /> : <><Save size={16} />{job ? "保存修改" : "保存职位"}</>}</button></div></div></>}
        </>}
      </form> : job && <div className="job-review-layout"><div className="job-readonly-requirements">{Object.entries(priorities).map(([priority, label]) => <section className="panel job-readonly-group" key={priority}><div className="section-heading"><div><h2>{priority === "must" ? "硬性要求" : priority === "preferred" ? "优先条件" : "待核实要求"}</h2><p>{label}分类由你审核和修正。</p></div><span className="badge badge-neutral">{job.requirements.filter(item => item.priority === priority).length} 项</span></div>{job.requirements.filter(item => item.priority === priority).map(item => <article key={item.id} className="job-readonly-condition" data-requirement-id={item.id}><div><span className="job-condition-type">{requirementTypes[item.type]}</span><span className={`badge ${item.review_status === "confirmed" && !item.stale ? "badge-success" : "badge-neutral"}`}>{item.stale ? "原文已更新 · 需重审" : item.review_status === "confirmed" ? "已确认" : "待审核"}</span></div><p>{item.content}</p>{item.evidence_text ? <div className="evidence-snippet"><strong>{item.stale ? "旧版 JD 引文，仅供核对" : "JD 原文证据"}</strong><blockquote>{item.evidence_text}</blockquote><span>JD v{item.source_jd_version}{item.line_start ? ` · 第 ${item.line_start}${item.line_end !== item.line_start ? `–${item.line_end}` : ""} 行 · 字符 ${item.source_start}–${item.source_end}` : ""}</span></div> : <p className="job-evidence-location">用户填写 · 未提供原文证据</p>}</article>)}{!job.requirements.some(item => item.priority === priority) && <p className="job-group-empty">暂无{label}条件，未说明的信息保持未知。</p>}</section>)}<button className="button button-secondary" onClick={() => setEditing(true)}><Pencil size={16} />审核和修正条件</button></div><aside className="panel job-original"><div className="section-heading"><div><h2>招聘原文</h2><p>当前 JD v{job.jd_version}</p></div></div><pre className="job-jd-text">{job.jd_text || "未提供 JD"}</pre>{job.jd_snapshots?.filter(snapshot => snapshot.version !== job.jd_version).map(snapshot => <details className="job-old-snapshot" key={snapshot.version}><summary>历史 JD v{snapshot.version} · {formatDate(snapshot.created_at)}</summary><pre className="job-jd-text">{snapshot.jd_text || "未提供 JD"}</pre></details>)}</aside></div>}
      <p className="job-bottom-note">规则整理与人工审核不代表岗位真实性验证。匹配只辅助决策，不代表录用概率。</p>
    </>}
    <Dialog.Root open={deleteOpen} onOpenChange={open => { if (!busy) setDeleteOpen(open); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content role="alertdialog" className="dialog-content compact-dialog"><Dialog.Title>删除这个职位？</Dialog.Title><Dialog.Description className="delete-description">“{job?.title}”的基本信息、JD 快照和结构化条件将删除。此操作无法在页面中撤销。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={busy}>保留职位</Dialog.Close><button className="button button-danger" disabled={busy} onClick={() => { void remove(); }}>{busy ? "正在删除…" : "确认删除职位"}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={resetOpen} onOpenChange={setResetOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content role="alertdialog" className="dialog-content compact-dialog"><Dialog.Title>重新载入最新职位？</Dialog.Title><Dialog.Description className="delete-description">本次尚未保存的修改将被放弃。需要保留的内容请先复制。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-primary">保留本次编辑</Dialog.Close><button className="button button-secondary" onClick={reset}>放弃修改并重新载入</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={parseOpen} onOpenChange={setParseOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content role="alertdialog" className="dialog-content compact-dialog"><Dialog.Title>重新整理当前 JD？</Dialog.Title><Dialog.Description className="delete-description">将替换当前编辑中的全部条件和审核进度。新规则结果全部为待审核；职位须点击保存后才会更新。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary">保留现有条件</Dialog.Close><button className="button button-primary" onClick={() => { void parse(); }}>重新整理并替换</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </WorkspaceShell>;
}
