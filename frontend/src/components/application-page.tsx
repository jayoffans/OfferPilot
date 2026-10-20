"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, ArrowUpRight, CalendarClock, Check, ClipboardList, RefreshCw, Save, Trash2, X } from "lucide-react";
import { api, ApiError, type Application, type ApplicationEvents, type ApplicationUpdate } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WorkspaceShell } from "@/components/workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { actionDue, actionStatuses, ApplicationBadge, applicationError, applicationStatuses, eventLabels, isTerminal, localDateTime, shanghaiInstant } from "@/components/applications-ui";

type Draft = { status: Application["status"]; submitted: string; notes: string; title: string; actionNotes: string; dueDate: string; dueTime: string; actionStatus: "pending" | "completed" | "cancelled"; occurred: string };
function toDraft(item: Application): Draft {
  return { status: item.status, submitted: localDateTime(item.submitted_at), notes: item.notes, title: item.next_action?.title || "", actionNotes: item.next_action?.notes || "", dueDate: item.next_action?.due_date || "", dueTime: item.next_action?.due_time?.slice(0, 5) || "", actionStatus: item.next_action?.status || "pending", occurred: "" };
}
function displayChange(value: unknown): string {
  if (value == null || value === "") return "未填写";
  if (typeof value === "string") return applicationStatuses[value as Application["status"]] || value;
  if (typeof value === "object" && !Array.isArray(value)) {
    const data = value as Record<string, unknown>;
    if (typeof data.title === "string") return [data.title, data.due_date ? `${data.due_date}${data.due_time ? ` ${String(data.due_time).slice(0, 5)}（北京时间）` : " · 仅日期"}` : "未设截止日期", actionStatuses[data.status as keyof typeof actionStatuses], data.notes].filter(Boolean).join(" · ");
    if (typeof data.status === "string") return applicationStatuses[data.status as Application["status"]] || data.status;
  }
  return String(value);
}

export function ApplicationPage({ id, backHref = "/applications" }: { id: string; backHref?: string }) {
  const { user, status } = useAuth();
  const userId = user?.id;
  const router = useRouter();
  const [item, setItem] = useState<Application | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<Application | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [eventRefresh, setEventRefresh] = useState(0);
  const [events, setEvents] = useState<ApplicationEvents | null>(null);
  const [eventPage, setEventPage] = useState(1);
  const [eventLoading, setEventLoading] = useState(true);
  const [eventError, setEventError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<"reload" | "delete" | "terminal" | "cancel" | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const attempt = useRef<{ serialized: string; key: string } | null>(null);
  const visible = owner === userId ? item : null;
  const dirty = !!visible && !!draft && JSON.stringify(draft) !== JSON.stringify(toDraft(visible));
  useEffect(() => () => { mutation.current?.abort(); attempt.current = null; pending.current = false; }, [userId, status]);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
    setLoading(true); setLoadError(null);
    void api.application(id, controller.signal).then(data => { if (!controller.signal.aborted) { setItem(data); setDraft(toDraft(data)); setOwner(userId); setConflict(false); setLatest(null); setError(null); setBusy(false); } }).catch(cause => { if (!controller.signal.aborted) { setLoadError(applicationError(cause)); setOwner(userId); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [id, userId, status, refresh]);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => { setEventLoading(true); setEventError(null);
    void api.applicationEvents(id, new URLSearchParams({ page: String(eventPage), page_size: "20" }), controller.signal).then(data => { if (!controller.signal.aborted) setEvents(data); }).catch(cause => { if (!controller.signal.aborted) setEventError(applicationError(cause)); }).finally(() => { if (!controller.signal.aborted) setEventLoading(false); });
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [id, userId, status, eventPage, eventRefresh, refresh]);
  function change<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft(current => current ? { ...current, [key]: value } : current); setSuccess(null); if (!conflict) setError(null); }
  function discard() { if (item) setDraft(toDraft(item)); }
  function fail(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 409) { setConflict(true); setError("申请记录已发生变化。本次输入已保留，尚未覆盖服务器内容。请核对最新记录后重新载入。"); }
    else setError(`${applicationError(cause)} 保存结果可能尚未确认；相同内容重试会复用本次请求，也可以先查询最新记录。`);
  }
  async function save(actionStatus?: Draft["actionStatus"]) {
    if (!visible || !draft || pending.current || conflict) return;
    if (draft.dueTime && !draft.dueDate) { setError("请先填写行动截止日期，再设置具体时间。"); return; }
    if (!draft.title.trim() && (draft.actionNotes.trim() || draft.dueDate || draft.dueTime)) { setError("请填写下一步行动名称，或清空该行动的日期和备注。"); return; }
    if ((draft.submitted && !shanghaiInstant(draft.submitted)) || (draft.occurred && !shanghaiInstant(draft.occurred))) { setError("请检查日期和时间格式。"); return; }
    const data: Omit<ApplicationUpdate, "idempotency_key"> = { revision: visible.revision, status: draft.status, submitted_at: draft.submitted === localDateTime(visible.submitted_at) ? visible.submitted_at : shanghaiInstant(draft.submitted), notes: draft.notes, next_action: draft.title.trim() ? { title: draft.title.trim(), notes: draft.actionNotes, due_date: draft.dueDate || null, due_time: draft.dueTime || null, timezone: "Asia/Shanghai", status: actionStatus || draft.actionStatus } : null, occurred_at: shanghaiInstant(draft.occurred) };
    const serialized = JSON.stringify({ id, ...data });
    if (attempt.current?.serialized !== serialized) attempt.current = { serialized, key: crypto.randomUUID() };
    pending.current = true; setBusy(true); setError(null); setSuccess(null); setConfirmation(null);
    const controller = new AbortController(); mutation.current = controller;
    try {
      const result = await api.updateApplication(id, { ...data, idempotency_key: attempt.current.key }, controller.signal);
      if (!controller.signal.aborted) { setItem(result); setDraft(toDraft(result)); attempt.current = null; setEventPage(1); setEventRefresh(value => value + 1); setSuccess("申请修改已保存，时间线已同步。刷新或重新登录后仍可继续跟踪。"); }
    } catch (cause) { if (!controller.signal.aborted) fail(cause); }
    finally { pending.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  async function readLatest() {
    if (pending.current) return;
    pending.current = true; setBusy(true);
    const controller = new AbortController(); mutation.current = controller;
    try { const data = await api.application(id, controller.signal); if (!controller.signal.aborted) setLatest(data); }
    catch (cause) { if (!controller.signal.aborted) setError(`最新记录暂时无法读取。${applicationError(cause)} 本次输入仍保留。`); }
    finally { pending.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  async function remove() {
    if (!visible || pending.current || conflict) return;
    const serialized = JSON.stringify({ delete: id, revision: visible.revision });
    if (attempt.current?.serialized !== serialized) attempt.current = { serialized, key: crypto.randomUUID() };
    pending.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); mutation.current = controller;
    try { await api.deleteApplication(id, { revision: visible.revision, idempotency_key: attempt.current.key, confirmed: true }, controller.signal); if (!controller.signal.aborted) { setConfirmation(null); discard(); router.replace("/applications"); } }
    catch (cause) { if (!controller.signal.aborted) { setConfirmation(null); fail(cause); } }
    finally { pending.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  function reload() { if (dirty || conflict) setConfirmation("reload"); else { setSuccess(null); setRefresh(value => value + 1); } }
  function submit() { if (draft && visible && ["withdrawn", "closed"].includes(draft.status) && draft.status !== visible.status) setConfirmation("terminal"); else void save(); }
  return <WorkspaceShell hasUnsavedChanges={dirty} onDiscard={discard}><Link className="back-link" href={backHref}><ArrowLeft size={15} />返回投递进度</Link><PageHeading eyebrow="持续跟踪，及时调整" title="申请详情" description="记录你确认的真实进展；状态可以跳转和纠正，每次修改都会留在时间线。" action={<button className="button button-secondary" disabled={busy || loading} onClick={reload}><RefreshCw size={16} />重新载入申请</button>} />
    {loading || owner !== userId ? <div className="loading-card"><Spinner label="正在读取申请详情" /></div> : loadError || !visible || !draft ? <section className="panel job-panel-body"><Notice>{loadError || "申请记录不可访问。"}</Notice><button className="button button-secondary" onClick={reload}>重试读取申请</button></section> : <>
      {error && <Notice>{error}</Notice>}{success && <Notice tone="success">{success}</Notice>}
      {conflict && <section className="profile-conflict"><div className="profile-conflict-actions"><button className="button button-secondary" disabled={busy} onClick={() => { void readLatest(); }}>查看最新申请记录</button><button className="text-button" disabled={busy} onClick={() => setConfirmation("reload")}>放弃本次输入，重新载入</button></div>{latest && <details className="profile-latest" open><summary>服务器最新申请（只读）</summary><p>状态：{applicationStatuses[latest.status]} · revision {latest.revision}</p><p>备注：{latest.notes || "未填写"}</p><p>下一步：{latest.next_action?.title || "未安排"}</p><p>实际投递：{latest.submitted_at ? formatDate(latest.submitted_at, true) : "未知"}</p></details>}</section>}
      <section className="panel application-overview"><div className="application-overview-top"><span className="job-icon job-icon-large"><ClipboardList size={27} /></span><div><h2>{visible.job_title}</h2><p>{visible.company || "公司待补充"} · {visible.location || "地点未知"}</p><div className="application-overview-badges"><ApplicationBadge status={visible.status} /><span>第 {visible.attempt_number} 次申请</span></div></div></div><div className="application-overview-links"><Link href={`/jobs/${visible.job_id}`} className="inline-link">返回职位<ArrowUpRight size={14} /></Link><Link href={`/jobs/${visible.job_id}#job-match`} className="inline-link">查看当前岗位匹配<ArrowUpRight size={14} /></Link></div><div className="job-provenance"><span>创建：{formatDate(visible.created_at, true)}</span><span>更新：{formatDate(visible.updated_at, true)}</span><span>revision {visible.revision}</span></div>{visible.match ? <div className="application-match-reference"><strong>{visible.match.stale ? "关联的匹配分析已失效，仅作历史参考" : "创建时关联的匹配分析"}</strong><p>{formatDate(visible.match.created_at, true)} · 画像 v{visible.match.profile_revision} / 职位 v{visible.match.job_revision} / JD v{visible.match.jd_version} · {visible.match.rule_version}</p><p>历史匹配不代表当前结论，也不会改变申请状态。</p></div> : <p className="field-hint">未关联匹配分析。你可以直接申请，无需先获得匹配建议。</p>}</section>
      <div className="application-detail-grid"><form onSubmit={event => { event.preventDefault(); submit(); }} className="application-form"><section className="panel application-editor"><div className="section-heading"><div><h2>申请进展</h2><p>按实际情况更新，允许跳过阶段或纠正误操作。</p></div></div><fieldset className="profile-form-fieldset" disabled={busy || conflict}><div className="profile-fields"><div className="form-field"><label htmlFor="application-status">申请状态</label><select id="application-status" value={draft.status} onChange={event => change("status", event.target.value as Draft["status"])}>{Object.entries(applicationStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div><div className="form-field"><label htmlFor="application-submitted">实际投递时间（北京时间）</label><input id="application-submitted" type="datetime-local" value={draft.submitted} onChange={event => change("submitted", event.target.value)} /><p className="field-hint">不知道具体时间可以留空；选择“已投递”不会自动补写。</p></div><div className="form-field profile-field-wide"><label htmlFor="application-notes">申请备注</label><textarea id="application-notes" rows={4} maxLength={10000} value={draft.notes} onChange={event => change("notes", event.target.value)} placeholder="记录投递渠道、沟通要点或需要留意的事情" /></div></div>{draft.status === "offer" && <p className="application-inline-note">收到 Offer 只表示获得意向或通知，不表示你已接受或签署。</p>}</fieldset></section>
        <section className="panel application-editor" id="application-next-action"><div className="section-heading"><div><h2><CalendarClock size={18} />下一步行动</h2><p>一次专注一项行动，之前的安排保留在时间线。</p></div>{visible.next_action && <span className={`badge ${visible.next_action.status === "completed" ? "badge-success" : "badge-neutral"}`}>{isTerminal(visible.status) && visible.next_action.status === "pending" ? "申请已结束，此行动不计入待办" : visible.is_overdue ? "已逾期 · 待完成" : actionStatuses[visible.next_action.status]}</span>}</div><fieldset className="profile-form-fieldset" disabled={busy || conflict}><div className="profile-fields"><div className="form-field profile-field-wide"><label htmlFor="application-action">下一步行动</label><input id="application-action" value={draft.title} maxLength={300} onChange={event => change("title", event.target.value)} placeholder="例如：准备技术面试、完成笔试、跟进 HR" /></div><div className="form-field"><label htmlFor="application-due-date">行动截止日期</label><input id="application-due-date" type="date" value={draft.dueDate} onChange={event => { change("dueDate", event.target.value); if (!event.target.value) change("dueTime", ""); }} /></div><div className="form-field"><label htmlFor="application-due-time">具体时间（可选）</label><input id="application-due-time" type="time" value={draft.dueTime} disabled={!draft.dueDate || busy || conflict} onChange={event => change("dueTime", event.target.value)} /><p className="field-hint">北京时间。留空即仅日期，不生成具体提醒时间。</p></div><div className="form-field profile-field-wide"><label htmlFor="application-action-notes">行动备注</label><textarea id="application-action-notes" rows={3} maxLength={2000} value={draft.actionNotes} onChange={event => change("actionNotes", event.target.value)} /></div></div></fieldset>{visible.next_action && <div className="application-action-tools"><p>{actionDue(visible.next_action)}</p>{visible.next_action.status === "pending" ? <><button type="button" className="button button-secondary" disabled={busy || conflict || dirty} onClick={() => { void save("completed"); }}><Check size={15} />完成行动</button><button type="button" className="text-button" disabled={busy || conflict || dirty} onClick={() => setConfirmation("cancel")}><X size={15} />取消行动</button></> : <><button type="button" className="button button-secondary" disabled={busy || conflict || dirty || isTerminal(visible.status)} onClick={() => { void save("pending"); }}>恢复待完成</button><button type="button" className="text-button" disabled={busy || conflict || dirty} onClick={() => setDraft(current => current ? { ...current, title: "", actionNotes: "", dueDate: "", dueTime: "", actionStatus: "pending" } : current)}>安排新行动</button></>}{isTerminal(visible.status) && <span className="field-hint">申请已结束，未完成行动会取消；历史待完成行动不计入待办，保存后会取消。请先重新开启申请，再恢复或安排待办。</span>}{dirty && <span className="field-hint">先保存当前修改，再完成或取消行动。</span>}</div>}</section>
        <details className="panel application-event-time"><summary>补记或纠正较早发生的事件</summary><div className="form-field"><label htmlFor="application-occurred">本次变化实际发生时间（北京时间）</label><input id="application-occurred" type="datetime-local" disabled={busy || conflict} value={draft.occurred} onChange={event => change("occurred", event.target.value)} /><p className="field-hint">可选。留空按本次记录时间；适用于本次保存产生的所有变化，系统记录时间始终另行保留。</p></div></details>
        <div className="profile-savebar application-savebar"><div><strong>{dirty ? "有修改尚未保存" : "已与服务器同步"}</strong><p>保存才会更新状态、备注和下一步行动。</p></div><button className="button button-primary" disabled={!dirty || busy || conflict} type="submit">{busy ? <Spinner label="正在保存" /> : <><Save size={16} />保存申请修改</>}</button></div>
      </form><aside className="panel application-timeline" aria-labelledby="application-timeline-title"><div className="section-heading"><div><h2 id="application-timeline-title">申请时间线</h2><p>保留每次变化，修改会追加记录。</p></div><button className="icon-button" aria-label="刷新申请时间线" disabled={eventLoading} onClick={() => setEventRefresh(value => value + 1)}><RefreshCw size={16} /></button></div>{eventError ? <div className="application-timeline-body"><Notice>{eventError}</Notice><button className="text-button" onClick={() => setEventRefresh(value => value + 1)}>重新读取时间线</button></div> : eventLoading ? <div className="application-timeline-body"><Spinner label="正在读取时间线" /></div> : <><ol className="application-events">{events?.items.map(event => <li key={event.id} className="application-event"><span className="application-event-dot" /><strong>{eventLabels[event.type] || "申请记录更新"}</strong><time dateTime={event.occurred_at}>实际发生：{formatDate(event.occurred_at, true)}</time><span>系统记录：{formatDate(event.recorded_at, true)} · v{event.revision}</span><details><summary>查看变化内容</summary>{event.type !== "application_created" && <div><b>修改前</b><p>{event.type === "submitted_at_changed" && typeof event.changes.before === "string" ? formatDate(event.changes.before, true) : displayChange(event.changes.before)}</p></div>}<div><b>{event.type === "application_created" ? "初始状态" : "修改后"}</b><p>{event.type === "submitted_at_changed" && typeof event.changes.after === "string" ? formatDate(event.changes.after, true) : displayChange(event.changes.after)}</p></div></details></li>)}</ol>{events && events.total > events.page_size && <div className="list-pagination"><span>第 {events.page} 页 · 共 {events.total} 条变化</span><div><button className="icon-button" aria-label="上一页时间线" disabled={eventPage <= 1} onClick={() => setEventPage(value => value - 1)}>‹</button><button className="icon-button" aria-label="下一页时间线" disabled={eventPage * events.page_size >= events.total} onClick={() => setEventPage(value => value + 1)}>›</button></div></div>}</>}</aside></div>
      <section className="application-danger"><div><strong>删除申请记录</strong><p>结束跟踪可将状态改为“已结束”。删除会清除本条申请和时间线，职位收藏不受影响。</p></div><button className="text-button danger-text" disabled={busy || conflict || dirty} onClick={() => setConfirmation("delete")}><Trash2 size={15} />删除申请记录</button></section>
    </>}
    <Dialog.Root open={!!confirmation} onOpenChange={open => { if (!open && !busy) setConfirmation(null); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content compact-dialog" role="alertdialog"><Dialog.Title>{confirmation === "reload" ? "放弃当前输入并重新载入？" : confirmation === "delete" ? "永久删除这条申请记录？" : confirmation === "cancel" ? "取消这项下一步行动？" : "确认结束本轮申请跟踪？"}</Dialog.Title><Dialog.Description className="delete-description">{confirmation === "reload" ? "尚未保存的输入会被放弃。请先复制需要保留的内容，再读取服务器最新记录。" : confirmation === "delete" ? "本条申请、备注、行动和全部时间线会被清除，无法在页面恢复。已保存职位和收藏保留。" : confirmation === "cancel" ? "行动会标记为已取消，历史安排仍可在时间线查看；以后可以恢复待完成。" : "此修改会写入时间线，之前的历史会保留。若操作有误，之后仍可纠正状态。"}</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={busy}>{confirmation === "reload" ? "保留当前输入" : "暂不操作"}</Dialog.Close><button className={`button ${confirmation === "delete" ? "button-danger" : "button-primary"}`} disabled={busy} onClick={() => { if (confirmation === "reload") { setConfirmation(null); setSuccess(null); attempt.current = null; setRefresh(value => value + 1); } else if (confirmation === "delete") void remove(); else if (confirmation === "cancel") void save("cancelled"); else void save(); }}>{busy ? "正在处理…" : confirmation === "reload" ? "放弃输入并重新载入" : confirmation === "delete" ? "确认删除申请" : confirmation === "cancel" ? "确认取消行动" : "确认保存状态"}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </WorkspaceShell>;
}
