"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { Archive, Check, Download, FileText, LockKeyhole, RefreshCw, ShieldCheck, Trash2, X } from "lucide-react";
import { api, ApiError, formatError, type AccountExport } from "@/lib/api";
import { navigateToDeletionStatus, useAuth } from "@/lib/auth";
import { WorkspaceShell } from "./workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "./ui";

const exportLabels: Record<AccountExport["status"], string> = { queued: "等待处理", running: "正在整理", ready: "可以下载", failed: "导出失败", expired: "文件已过期", cancelled: "导出已取消" };
const exportScope = [
  ["账户与设置", "基本账户信息、创建时间及已保存的求职偏好"],
  ["简历与来源", "PDF 原件、文件信息、解析内容、候选与证据"],
  ["求职画像", "正式资料、手工内容、事实来源及必要的修订信息"],
  ["已保存职位", "JD 原文、结构化条件、审核和收藏状态"],
  ["匹配分析", "逐项判断、关联证据、规则版本及历史结果"],
  ["投递与行动", "申请记录、备注、状态时间线及行动完成记录"],
];

function exportFailure(code: string | null) {
  if (code?.includes("limit") || code?.includes("quota")) return "本次资料超出导出资源限制。请联系维护者核查，再重新申请。";
  return "导出未完成，尚无可下载文件。可以重新验证密码后申请新导出；如果持续失败，请联系维护者。";
}

export function AccountSettings() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [exports, setExports] = useState<AccountExport[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [downloadId, setDownloadId] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [deletionOpen, setDeletionOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deletionError, setDeletionError] = useState<string | null>(null);
  const [deletionUnknown, setDeletionUnknown] = useState(false);
  const createKey = useRef<string | null>(null);
  const deletionKey = useRef<string | null>(null);
  const inFlight = useRef(false);
  const actionController = useRef<AbortController | null>(null);
  const task = exports[0] ?? null;
  const active = exports.some(item => ["queued", "running"].includes(item.status));

  useEffect(() => () => actionController.current?.abort(), []);
  const load = useCallback(async (signal?: AbortSignal) => {
    const items = await api.accountExports(signal);
    if (!signal?.aborted) { setExports(items); setLoadError(null); }
  }, []);
  useEffect(() => {
    if (status !== "authenticated") return;
    const controller = new AbortController();
    const timer = setTimeout(() => { void load(controller.signal).catch(cause => { if (!controller.signal.aborted) setLoadError(formatError(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [load, status, user?.id]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (++attempts > 20 || controller.signal.aborted) return;
      try { await load(controller.signal); }
      catch (cause) { if (!controller.signal.aborted) setLoadError(formatError(cause)); return; }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 1500);
    }
    timer = setTimeout(() => { void poll(); }, 1500);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [active, load, task?.id]);

  async function refresh() {
    if (refreshing || inFlight.current) return;
    setRefreshing(true);
    try { await load(); } catch (cause) { setLoadError(formatError(cause)); }
    finally { setRefreshing(false); }
  }
  async function createExport(event: React.FormEvent) {
    event.preventDefault();
    if (!password || inFlight.current || active) return;
    inFlight.current = true; setCreating(true); setExportError(null); setDownloaded(false);
    createKey.current ??= crypto.randomUUID();
    const controller = new AbortController(); actionController.current = controller;
    const currentPassword = password; setPassword("");
    try {
      const result = await api.createExport({ password: currentPassword, idempotency_key: createKey.current }, controller.signal);
      if (!controller.signal.aborted) { setExports(current => [result, ...current.filter(item => item.id !== result.id)]); createKey.current = null; }
    } catch (cause) {
      if (!controller.signal.aborted) setExportError(`${formatError(cause)}${!(cause instanceof ApiError) || cause.status >= 500 ? " 结果可能尚未确认，请先刷新导出状态；再次申请会复用本次请求。" : ""}`);
    } finally { inFlight.current = false; if (!controller.signal.aborted) setCreating(false); }
  }
  async function download(item: AccountExport) {
    if (downloadId || inFlight.current) return;
    setDownloadId(item.id); setExportError(null);
    try { await api.downloadExport(item.id); setDownloaded(true); }
    catch (cause) {
      setExportError(formatError(cause));
      if (cause instanceof ApiError && cause.status === 410) setExports(current => current.map(entry => entry.id === item.id ? { ...entry, status: "expired", download_available: false } : entry));
    } finally { setDownloadId(null); }
  }
  function closeDeletion(open: boolean) {
    if (deleting) return;
    setDeletionOpen(open);
    if (!open) { setDeletePassword(""); setConfirmation(""); setUnderstood(false); setDeletionError(null); }
  }
  async function finishDeletion() {
    setDeletePassword(""); setConfirmation("");
    navigateToDeletionStatus();
  }
  async function deleteAccount(event: React.FormEvent) {
    event.preventDefault();
    if (!deletePassword || confirmation !== "注销账户" || !understood || inFlight.current) return;
    inFlight.current = true; setDeleting(true); setDeletionError(null); setDeletionUnknown(false);
    deletionKey.current ??= crypto.randomUUID();
    const controller = new AbortController(); actionController.current = controller;
    const currentPassword = deletePassword; setDeletePassword("");
    try {
      await api.createDeletion({ password: currentPassword, confirmation: "注销账户", idempotency_key: deletionKey.current }, controller.signal);
      if (!controller.signal.aborted) await finishDeletion();
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (!(cause instanceof ApiError) || cause.status >= 500 || cause.status === 401) {
        try { await api.deletionStatus(controller.signal); if (!controller.signal.aborted) { await finishDeletion(); return; } } catch { /* Read-only recovery may be unavailable; never repeat a destructive request automatically. */ }
        setDeletionUnknown(true);
        setDeletionError("注销结果尚未确认。请先查看处理状态；系统不会自动再次提交注销。若状态凭据已到期，请联系维护者核查。");
      } else setDeletionError(formatError(cause));
    } finally { inFlight.current = false; if (!controller.signal.aborted) setDeleting(false); }
  }

  return <WorkspaceShell><PageHeading eyebrow="资料由你管理" title="账户设置" description="保存一份求职资料副本，或管理账户及数据的删除。" /><section className="panel account-summary"><span className="account-section-icon"><ShieldCheck size={23} /></span><div><h2>你的账户</h2><p className="account-address">{user?.email}</p><p>创建于 {user ? formatDate(user.created_at) : "—"} · 邀请制个人账户</p></div><span className="badge badge-success">当前已登录</span></section><div className="account-settings-grid"><section className="panel account-data-panel" aria-labelledby="export-heading"><div className="section-heading"><div className="account-section-heading"><span className="account-section-icon"><Archive size={22} /></span><div><h2 id="export-heading">导出我的数据</h2><p>将你保存的主要求职资料整理为 ZIP 文件。</p></div></div><span className="badge badge-neutral">UTF-8 · JSON + PDF</span></div><div className="account-panel-body"><div className="account-export-scope">{exportScope.map(([title, description]) => <div key={title}><Check size={15} aria-hidden="true" /><div><strong>{title}</strong><p>{description}</p></div></div>)}</div><div className="account-data-note"><LockKeyhole size={16} /><p>只包含当前账户的数据，不导出密码、会话令牌或 API Key。下载窗口最长 24 小时，以本次到期时间为准。过期文件会安排清理，清理失败需维护者恢复。下载后请妥善保存。</p></div><form className="account-reauth-form" onSubmit={event => { void createExport(event); }}><label htmlFor="export-password">验证当前密码</label><p id="export-password-hint">导出包含个人资料，需要重新验证身份。</p><div className="account-reauth-row"><input id="export-password" type="password" autoComplete="current-password" value={password} maxLength={128} required disabled={creating || active || deleting} aria-describedby="export-password-hint" onChange={event => setPassword(event.target.value)} /><button type="submit" className="button button-primary" disabled={!password || creating || active || deleting}>{creating ? <Spinner label="正在申请" /> : <><Archive size={16} />{task && ["failed", "expired", "cancelled"].includes(task.status) ? "重新申请导出" : "申请导出"}</>}</button></div></form>{exportError && <Notice>{exportError}</Notice>}{downloaded && <Notice tone="success">已发起 ZIP 下载。请在浏览器下载列表确认文件保存完成。</Notice>}<div className="account-export-tasks"><div className="account-task-heading"><h3>最近导出</h3><button className="text-button" disabled={refreshing || creating || deleting} onClick={() => { void refresh(); }}><RefreshCw size={14} />{refreshing ? "正在刷新" : "刷新导出状态"}</button></div>{loadError && <Notice>{loadError}</Notice>}{loading ? <Spinner label="正在读取导出状态" /> : !exports.length ? <p className="account-empty">还没有导出记录。验证密码后即可申请。</p> : exports.map(item => <article key={item.id} className="account-export-task" aria-label={`导出任务 ${item.id}`}><div><div className="account-task-title"><FileText size={17} /><strong>求职资料 ZIP</strong><span className={`badge ${item.status === "ready" ? "badge-success" : "badge-neutral"}`}>{exportLabels[item.status]}</span></div><p>申请于 {formatDate(item.created_at, true)}</p>{item.expires_at && <p>有效期至 {formatDate(item.expires_at, true)}（北京时间）</p>}{["queued", "running"].includes(item.status) && <p role="status">正在整理你的资料，请稍候。自动查询有次数上限，可手动刷新继续查看。</p>}{item.status === "failed" && <p className="account-task-error" role="alert">{exportFailure(item.error_code)}</p>}{item.status === "expired" && <p>下载窗口已结束，原链接不可用。需要副本时请重新申请导出。</p>}{item.status === "cancelled" && <p>本次导出已停止，没有可下载文件。</p>}{item.status === "ready" && <p>请查看 ZIP 中的 manifest.json，确认原件及未完成资料的导出范围。</p>}</div>{item.download_available && item.status === "ready" && <button className="button button-secondary" disabled={!!downloadId || deleting} onClick={() => { void download(item); }}>{downloadId === item.id ? <Spinner label="正在下载" /> : <><Download size={16} />下载 ZIP</>}</button>}</article>)}</div></div></section><section className="panel account-delete-panel" aria-labelledby="delete-heading"><div className="section-heading"><div className="account-section-heading"><span className="account-section-icon account-danger-icon"><Trash2 size={21} /></span><div><h2 id="delete-heading">注销账户</h2><p>结束账户使用，并删除该账户的求职资料。</p></div></div></div><div className="account-panel-body"><p>注销会撤销所有登录会话、会话级 Key 与后台任务访问，清理账户、简历原件及解析内容、画像候选与证据、职位、匹配、投递及导出文件。</p><p>此操作完成后无法恢复。建议先导出并确认已保存需要的资料，再继续注销。</p><div className="account-retention"><strong>删除的实际边界</strong><p>主数据库和私有文件按流程清理。备份由维护者隔离保留，当前环境尚未启用自动到期清理；恢复后开放访问前必须重放删除清单。最小删除及故障记录会保留。</p><p>已发给外部模型的请求可能无法撤回，供应商侧保留需遵循其处理政策；本产品不能保证立即删除供应商副本。</p></div><button className="button button-danger-quiet" disabled={creating || deleting} onClick={() => closeDeletion(true)}><Trash2 size={16} />查看注销说明并继续</button><p className="account-logout-note">如果只是暂时离开，请使用账户菜单中的“退出登录”，它会保留你的资料。</p></div></section></div><Dialog.Root open={deletionOpen} onOpenChange={closeDeletion}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content account-delete-dialog" role="alertdialog"><Dialog.Title>确认注销账户</Dialog.Title><Dialog.Description className="delete-description">请先确认需要的资料已导出。注销开始后将立即撤销普通访问，资料清理可能继续处理；只有全部完成才显示注销完成。</Dialog.Description><button className="icon-button dialog-close" aria-label="关闭注销确认" disabled={deleting} onClick={() => closeDeletion(false)}><X size={18} /></button><form onSubmit={event => { void deleteAccount(event); }}><label className="account-field" htmlFor="delete-password">当前密码<input id="delete-password" type="password" autoComplete="current-password" value={deletePassword} maxLength={128} required disabled={deleting || deletionUnknown} onChange={event => setDeletePassword(event.target.value)} /></label><label className="account-field" htmlFor="delete-confirmation">输入“注销账户”以确认<input id="delete-confirmation" autoComplete="off" value={confirmation} maxLength={20} required disabled={deleting || deletionUnknown} onChange={event => setConfirmation(event.target.value)} /></label><label className="account-confirm-check"><input type="checkbox" checked={understood} disabled={deleting || deletionUnknown} onChange={event => setUnderstood(event.target.checked)} /><span>我已了解删除范围及备份、第三方保留边界，确认注销此账户。</span></label>{deletionError && <Notice>{deletionError}</Notice>}{deletionUnknown && <button type="button" className="button button-secondary" onClick={() => router.replace("/account-deletion")}>查看注销处理状态</button>}<div className="dialog-actions"><button type="button" className="button button-secondary" disabled={deleting} onClick={() => closeDeletion(false)}>保留账户</button><button type="submit" className="button button-danger" disabled={!deletePassword || confirmation !== "注销账户" || !understood || deleting || deletionUnknown}>{deleting ? <Spinner label="正在发起注销" /> : "确认注销并删除数据"}</button></div></form></Dialog.Content></Dialog.Portal></Dialog.Root></WorkspaceShell>;
}
