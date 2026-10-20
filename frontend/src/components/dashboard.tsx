"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, ArrowUpRight, CalendarClock, Check, ClipboardList, Plus, RefreshCw, Upload } from "lucide-react";
import { api, ApiError, SESSION_RESET_EVENT, type ApplicationUpdate, type DashboardData } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WorkspaceShell } from "@/components/workspace-shell";
import { UploadDialog } from "@/components/upload-dialog";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { actionDue, actionStatuses, ApplicationBadge, applicationError, applicationStatuses, eventLabels, isTerminal } from "@/components/applications-ui";

type ActionItem = DashboardData["actions"]["today"]["items"][number];
type ActionStatus = "pending" | "completed" | "cancelled";
const groupLabels = { overdue: "已逾期", today: "今天需要完成", upcoming: "未来七天", undated: "未设截止日期", completed: "已完成", cancelled: "已取消" } as const;
const jobReasons = { requirements_missing: "尚无结构化条件，可检查或补充 JD", favorite_without_application: "已收藏，尚未创建申请", review_pending: "JD 条件待审核", match_stale: "匹配依据已变更", unmatched: "已审核，尚未匹配" };
const progressLabels = { preparing: "准备投递", submitted: "已投递", assessment_interview: "笔试或面试", offer: "收到 Offer", ended: "已结束" } as const;

function guide(data: DashboardData) {
  const job = data.onboarding.job_id;
  const choices = {
    upload_resume: { title: "从一份简历开始，也可以手工建立资料", description: "上传文本型 PDF 后确认事实；已有资料也可以直接保存职位。", label: "上传第一份简历", href: null },
    complete_profile: { title: "把真实经历整理为求职资料", description: "可以手工补充教育、技能与经历；已有简历可继续沿用，AI 候选须由你确认。", label: "完善我的资料", href: "/profile" },
    import_job: { title: "资料已保存，开始了解一个机会", description: "粘贴招聘 JD，审核要求，再查看与已确认事实的匹配依据。", label: "导入第一个岗位", href: "/jobs/new" },
    review_job: { title: "先确认职位的真实要求", description: "检查并补充结构化条件；规则整理的内容须由你审核，待核实或过期条件不会用于可信匹配。", label: "审核 JD 条件", href: job ? `/jobs/${job}` : "/jobs" },
    match_job: { title: "查看这个职位与你的匹配依据", description: "确定性匹配区分明确满足、部分满足、未发现证据和明确不满足，不代表录用概率。", label: "查看岗位匹配", href: job ? `/jobs/${job}#job-match` : "/jobs" },
    create_application: { title: "认真考虑的机会，可以开始记录", description: "收藏表示关注。点击职位内的“准备投递”才会创建申请，实际提交由你完成。", label: "创建投递计划", href: job ? `/jobs/${job}` : "/jobs" },
    manage_actions: { title: "让每个申请都有清楚的下一步", description: "安排、完成或调整跟进事项。记录进展不会自动向招聘方提交申请或发送消息。", label: "管理申请与下一步", href: "/applications" },
  };
  return choices[data.onboarding.next_step];
}

export function Dashboard() {
  const { user, status } = useAuth();
  const userId = user?.id;
  const [data, setData] = useState<DashboardData | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [conflicted, setConflicted] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<ActionItem | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  const reading = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const attempt = useRef<{ id: string; status: ActionStatus; payload: ApplicationUpdate } | null>(null);
  const visible = status === "authenticated" && owner === userId ? data : null;

  useEffect(() => {
    const reset = () => { reading.current?.abort(); mutation.current?.abort(); pending.current = false; attempt.current = null; setData(null); setOwner(null); setCancelTarget(null); setSuccess(null); setActionError(null); setBusy(null); };
    window.addEventListener(SESSION_RESET_EVENT, reset);
    return () => { window.removeEventListener(SESSION_RESET_EVENT, reset); mutation.current?.abort(); attempt.current = null; pending.current = false; };
  }, [userId, status]);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController(); reading.current = controller;
    const timer = setTimeout(() => {
      setLoading(true); setError(null); setActionError(null); setSuccess(null); setBusy(null); setCancelTarget(null);
      void api.dashboard(controller.signal).then(result => {
        if (!controller.signal.aborted) { setData(result); setOwner(userId); setConflicted(false); }
      }).catch(cause => { if (!controller.signal.aborted) setError(applicationError(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [status, userId, refresh]);

  async function changeAction(item: ActionItem, nextStatus: ActionStatus) {
    if (!visible || pending.current || conflicted || (nextStatus === "pending" && isTerminal(item.application_status))) return;
    pending.current = true; setBusy(item.application_id); setActionError(null); setSuccess(null); setCancelTarget(null);
    const controller = new AbortController(); mutation.current = controller;
    try {
      if (attempt.current?.id !== item.application_id || attempt.current.status !== nextStatus) {
        const latest = await api.application(item.application_id, controller.signal);
        if (controller.signal.aborted) return;
        if (latest.revision !== item.revision || !latest.next_action || (["title", "notes", "due_date", "due_time", "timezone", "status"] as const).some(key => latest.next_action?.[key] !== item.next_action[key])) throw new ApiError(409, "application_conflict", "这条行动已发生变化，请刷新工作台核对后再操作。");
        attempt.current = { id: item.application_id, status: nextStatus, payload: { revision: latest.revision, status: latest.status, submitted_at: latest.submitted_at, notes: latest.notes, next_action: { ...item.next_action, status: nextStatus }, occurred_at: null, idempotency_key: crypto.randomUUID() } };
      }
      await api.updateApplication(item.application_id, attempt.current.payload, controller.signal);
      if (controller.signal.aborted) return;
      attempt.current = null;
      const result = await api.dashboard(controller.signal);
      if (!controller.signal.aborted) { setData(result); setOwner(userId || null); setSuccess(`行动${nextStatus === "pending" ? "已恢复为待完成" : nextStatus === "completed" ? "已完成" : "已取消"}，工作台已同步。`); }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof ApiError && cause.status === 409) { attempt.current = null; setConflicted(true); setActionError("申请或行动已在其他页面更新。请刷新工作台核对最新内容，再进行操作。"); }
        else setActionError(`${applicationError(cause)} 操作结果可能尚未确认；请刷新核对，或以相同按钮重试原请求。`);
      }
    } finally { pending.current = false; if (!controller.signal.aborted) setBusy(null); }
  }
  function reload() { if (pending.current) return; attempt.current = null; setActionError(null); setSuccess(null); setRefresh(value => value + 1); }
  const onboarding = visible ? guide(visible) : null;
  const guidance = onboarding && <section className="workbench-guide" aria-label="建议的下一步"><div><span className="hero-label">建议的下一步 · 可以跳过</span><h2>{onboarding.title}</h2><p>{onboarding.description}</p></div>{onboarding.href ? <Link href={onboarding.href} className="button button-secondary">{onboarding.label}<ArrowRight size={16} /></Link> : <button className="button button-primary" onClick={() => setUploadOpen(true)}><Upload size={16} />{onboarding.label}</button>}</section>;
  const pendingTotal = visible ? visible.actions.overdue.total + visible.actions.today.total + visible.actions.upcoming.total + visible.actions.undated.total : 0;

  return <WorkspaceShell>
    <PageHeading eyebrow={visible ? `${visible.today} · 北京时间` : "每天的下一步，在这里看清"} title="工作台" description="先处理眼前的行动，再继续认真考虑的机会。" action={<Link className="button button-primary" href="/jobs/new"><Plus size={17} />导入职位</Link>} />
    {error ? <section className="panel job-panel-body"><h2>工作台暂时无法读取</h2><Notice>{error}</Notice><button className="button button-secondary" onClick={reload}>重新加载工作台</button></section> : loading || !visible ? <div className="loading-card"><Spinner label="正在读取你的求职工作台" /></div> : <>
      {actionError && <Notice>{actionError}<button className="text-button" disabled={!!busy} onClick={reload}>刷新工作台</button></Notice>}
      {success && <Notice tone="success">{success}</Notice>}
      {!visible.summary.total && guidance}
      <section className="panel workbench-actions" aria-labelledby="workbench-actions-heading">
        <div className="section-heading"><div><h2 id="workbench-actions-heading"><CalendarClock size={20} />今日与近期行动</h2><p>已完成和已取消的行动不计入待办。日期默认北京时间，仅日期不会生成具体时间。</p></div><button className="icon-button" disabled={!!busy} aria-label="刷新工作台" onClick={reload}><RefreshCw size={17} /></button></div>
        {!pendingTotal && <div className="workbench-no-actions"><Check size={22} /><div><strong>当前没有今日、逾期或近期待办</strong><p>可以查看已有申请，安排下一步；或继续了解一个职位。</p></div><Link href="/applications" className="inline-link">安排下一步<ArrowRight size={14} /></Link></div>}
        <div className="workbench-action-groups">{(["overdue", "today", "upcoming", "undated"] as const).filter(key => visible.actions[key].total > 0).map(key => <ActionGroup key={key} name={groupLabels[key]} group={visible.actions[key]} overdue={key === "overdue"} busy={!!busy} conflicted={conflicted} onChange={changeAction} onCancel={setCancelTarget} />)}</div>
        {!!(visible.actions.completed.total || visible.actions.cancelled.total) && <details className="workbench-history"><summary>已完成与已取消的行动（{visible.actions.completed.total + visible.actions.cancelled.total}）</summary>{(["completed", "cancelled"] as const).map(key => <ActionGroup key={key} name={groupLabels[key]} group={visible.actions[key]} busy={!!busy} conflicted={conflicted} onChange={changeAction} onCancel={setCancelTarget} />)}</details>}
        <div className="workbench-section-footer"><Link href="/applications?next_action=pending&sort=next_action_asc" className="subtle-link">查看全部待办申请<ArrowUpRight size={15} /></Link><span>七天范围：明天起至第七天；更晚行动见投递进度。</span></div>
      </section>
      {!!visible.summary.total && guidance}
      <section className="panel workbench-progress" aria-labelledby="workbench-progress-heading"><div className="section-heading"><div><h2 id="workbench-progress-heading">求职进展</h2><p>共 {visible.summary.total} 条独立申请；收藏不计入申请。收到 Offer 不表示已经接受。</p></div><Link href="/applications" className="subtle-link">查看投递进度<ArrowUpRight size={15} /></Link></div><dl>{Object.entries(progressLabels).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{visible.summary[key as keyof typeof progressLabels]}</dd></div>)}</dl>{!visible.summary.total && <p className="workbench-progress-empty">还没有申请记录。打开一个已保存职位，确认选择后创建投递计划。</p>}</section>
      <div className="workbench-bottom-grid"><section className="panel" aria-labelledby="workbench-jobs-heading"><div className="section-heading"><div><h2 id="workbench-jobs-heading">需要继续处理的职位</h2><p>依据审核、匹配和申请的真实记录。</p></div><Link href="/jobs" className="subtle-link">全部职位<ArrowUpRight size={15} /></Link></div>{visible.jobs.items.length ? <ul className="workbench-jobs">{visible.jobs.items.map(job => <li key={job.id}><div><strong>{job.title}</strong><p>{job.company || "公司待补充"}</p><div className="workbench-job-reasons">{job.reasons.map(reason => <span key={reason}>{jobReasons[reason]}</span>)}</div></div><Link href={`/jobs/${job.id}${job.next_step === "match" ? "#job-match" : ""}`} className="inline-link">{job.next_step === "review" ? "审核 JD" : job.next_step === "match" ? "查看匹配" : "查看并准备投递"}<ArrowRight size={14} /></Link></li>)}</ul> : <div className="workbench-empty"><ClipboardList size={24} /><p>{visible.onboarding.job_count ? "当前没有需要继续处理的职位。你仍可查看所有职位。" : "保存一份 JD，开始了解值得考虑的机会。"}</p><Link href="/jobs/new" className="inline-link">导入职位<ArrowRight size={14} /></Link></div>}{visible.jobs.total > visible.jobs.items.length && <p className="workbench-truncated">显示最近 {visible.jobs.items.length} 个，共 {visible.jobs.total} 个；其余请前往职位列表。</p>}</section>
      <section className="panel" aria-labelledby="workbench-activity-heading"><div className="section-heading"><div><h2 id="workbench-activity-heading">最近活动</h2><p>来自已保存的申请时间线，按记录时间展示。</p></div></div>{visible.recent_activity.length ? <ol className="workbench-activity">{visible.recent_activity.map(event => <li key={event.id}><span className="application-event-dot" /><div><Link href={`/applications/${event.application_id}`}><strong>{eventLabels[event.type] || "更新申请"}{event.type === "next_action_changed" && event.action_status ? ` · ${actionStatuses[event.action_status]}` : event.type === "status_changed" && event.application_status ? ` · ${applicationStatuses[event.application_status]}` : ""}</strong><p>{event.company || "公司待补充"} · {event.job_title}</p></Link><time dateTime={event.recorded_at}>{formatDate(event.recorded_at, true)}（北京时间）</time></div></li>)}</ol> : <div className="workbench-empty"><p>创建申请、更新状态和行动后，这里会显示真实记录。</p></div>}</section></div>
    </>}
    <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} />
    <Dialog.Root open={!!cancelTarget} onOpenChange={open => { if (!open) setCancelTarget(null); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content compact-dialog" role="alertdialog"><Dialog.Title>取消这条行动？</Dialog.Title><Dialog.Description className="delete-description">取消“{cancelTarget?.next_action.title}”后，它将移出待办。申请继续保留，你也可以恢复行动。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary">保留行动</Dialog.Close><button className="button button-primary" onClick={() => { if (cancelTarget) void changeAction(cancelTarget, "cancelled"); }}>确认取消行动</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </WorkspaceShell>;
}

function ActionGroup({ name, group, overdue = false, busy, conflicted, onChange, onCancel }: { name: string; group: DashboardData["actions"]["today"]; overdue?: boolean; busy: boolean; conflicted: boolean; onChange(item: ActionItem, status: ActionStatus): Promise<void>; onCancel(item: ActionItem): void }) {
  return <section className={`workbench-action-group${overdue ? " workbench-overdue" : ""}`} aria-label={name}><h3>{name}<span>{group.total}</span></h3>{group.items.length ? <div>{group.items.map(item => <article className="workbench-action" key={item.application_id} aria-label={item.next_action.title}><div><h4><Link href={`/applications/${item.application_id}`}>{item.next_action.title}</Link></h4><p>{item.company || "公司待补充"} · {item.job_title}</p><p className="workbench-due">{actionDue(item.next_action)}<span>{overdue ? "已逾期 · 待完成" : actionStatuses[item.next_action.status]}</span></p></div><ApplicationBadge status={item.application_status} /><div className="workbench-action-tools">{item.next_action.status === "pending" ? <><button className="button button-secondary" disabled={busy || conflicted} onClick={() => { void onChange(item, "completed"); }}><Check size={14} />完成行动</button><button className="text-button" disabled={busy || conflicted} onClick={() => onCancel(item)}>取消行动</button></> : <button className="button button-secondary" disabled={busy || conflicted || isTerminal(item.application_status)} onClick={() => { void onChange(item, "pending"); }}>恢复待完成</button>}<Link href={`/applications/${item.application_id}#application-next-action`} className="inline-link">修改下一步</Link><Link href={`/applications/${item.application_id}`} className="inline-link">查看申请<ArrowUpRight size={13} /></Link>{isTerminal(item.application_status) && item.next_action.status !== "pending" && <small>申请已结束，重新开启后可恢复行动。</small>}</div></article>)}</div> : <p className="workbench-group-empty">{overdue ? "没有逾期行动" : name === "今天需要完成" ? "今天暂无待完成行动" : name === "未来七天" ? "未来七天暂无安排" : "暂无记录"}</p>}{group.total > group.items.length && <p className="workbench-truncated">显示 {group.items.length} 条，共 {group.total} 条。<Link href={`/applications?next_action=${group.items[0]?.next_action.status || "pending"}${overdue ? "&overdue=true" : ""}&sort=next_action_asc`}>查看其余申请</Link></p>}</section>;
}
