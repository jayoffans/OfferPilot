"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, CircleCheck, Download, FileText, Pencil, RefreshCw, ShieldCheck, Trash2, X } from "lucide-react";
import { api, ApiError, formatError, type Resume, type ResumeLifecycle } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WorkspaceShell } from "@/components/workspace-shell";
import { FileIcon, formatDate, Notice, PrivacyNote, Spinner, validPdfName } from "@/components/ui";

function processingText(status: ResumeLifecycle["processing_status"]) {
  return { uploading: "文件上传中", uploaded: "文件已保存，正在读取文本", parsed: "文本解析完成", failed: "文件处理失败" }[status || "failed"];
}

function lifecycleText(status: ResumeLifecycle["lifecycle_status"]) {
  return { active: "可用", deleting: "删除处理中", delete_failed: "删除失败", deleted: "已删除" }[status];
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export function ResumeDetail({ id }: { id: string }) {
  const { status } = useAuth();
  const [resume, setResume] = useState<Resume | null>(null);
  const [lifecycle, setLifecycle] = useState<ResumeLifecycle | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [pollStopped, setPollStopped] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [pending, setPending] = useState<"rename" | "delete" | "download" | null>(null);
  const actionController = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const renameButton = useRef<HTMLButtonElement | null>(null);
  const deleteButton = useRef<HTMLButtonElement | null>(null);
  const deleted = lifecycle?.lifecycle_status === "deleted";
  const hidden = lifecycle && lifecycle.lifecycle_status !== "active";
  const canManage = !!resume && (!lifecycle || lifecycle.lifecycle_status === "active") && !deleted;
  const needsCleanup = lifecycle?.lifecycle_status === "delete_failed" || lifecycle?.lifecycle_status === "deleting" || lifecycle?.processing_status === "failed";

  useEffect(() => () => actionController.current?.abort(), []);
  useEffect(() => {
    if (status !== "authenticated") return;
    const controller = new AbortController();
    async function load() {
      let count = 0;
      try {
        while (!controller.signal.aborted) {
          const [metadata, state] = await Promise.allSettled([api.resume(id, controller.signal), api.lifecycle(id, controller.signal)]);
          if (controller.signal.aborted) return;
          const current = state.status === "fulfilled" ? state.value : null;
          setResume(metadata.status === "fulfilled" ? metadata.value : null);
          setLifecycle(current);
          if (metadata.status === "rejected" && !current) {
            const inaccessible = metadata.reason instanceof ApiError && metadata.reason.status === 404 && state.status === "rejected" && state.reason instanceof ApiError && state.reason.status === 404;
            setLoadError(inaccessible ? "找不到这份简历，可能已被删除或当前账户无法访问。" : formatError(metadata.reason));
          } else if (state.status === "rejected") setLoadError(`文件状态暂时无法读取。${formatError(state.reason)}`);
          else if (metadata.status === "rejected" && current?.lifecycle_status === "active" && current.processing_status === "parsed") setLoadError(`文件信息暂时无法读取。${formatError(metadata.reason)}`);
          else setLoadError(null);
          setLoading(false);
          const inProgress = current?.lifecycle_status === "deleting" || (current?.lifecycle_status === "active" && (current.processing_status === "uploading" || current.processing_status === "uploaded"));
          if (!inProgress) return;
          if (++count >= 10) { setPollStopped(true); return; }
          await delay(2000, controller.signal);
        }
      } catch (cause) { if (!controller.signal.aborted) { setLoadError(formatError(cause)); setLoading(false); } }
    }
    void load();
    return () => controller.abort();
  }, [id, revision, status]);

  function refresh() { if (!busy.current) { setLoading(true); setLoadError(null); setPollStopped(false); setRevision((value) => value + 1); } }

  async function mutate(action: "rename" | "delete" | "download") {
    if (busy.current) return;
    if (action === "rename") { const issue = validPdfName(newName); setNameError(issue); if (issue) return; }
    busy.current = true; setPending(action); setActionError(null); setSuccess(null);
    const controller = new AbortController(); actionController.current = controller;
    try {
      if (action === "rename") {
        const changed = await api.rename(id, { file_name: newName.trim() }, controller.signal);
        if (!controller.signal.aborted) { setResume(changed); setRenameOpen(false); setSuccess("文件名已更新，原始文件内容保持不变。"); }
      } else if (action === "download") {
        await api.download(id, resume?.file_name || "resume.pdf", controller.signal);
        if (!controller.signal.aborted) setSuccess("原件已准备好，请查看浏览器下载记录。");
      } else {
        await api.remove(id, controller.signal);
        if (!controller.signal.aborted) { setDeleteOpen(false); setResume(null); setSuccess("简历原件及关联数据已删除。"); setRevision((value) => value + 1); }
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setActionError(formatError(cause));
        if (action === "delete") { setDeleteOpen(false); setRevision((value) => value + 1); }
        if (cause instanceof ApiError && (cause.status === 404 || cause.status === 409 || cause.status === 503)) setRevision((value) => value + 1);
      }
    } finally { busy.current = false; if (!controller.signal.aborted) setPending(null); }
  }

  return <WorkspaceShell><Link href="/resumes" className="back-link"><ArrowLeft size={16} />我的简历</Link><div className="detail-page-heading"><div><p className="eyebrow">文件管理</p><h1>简历详情</h1><p className="page-description">查看文件状态，管理你的原始资料。</p></div><button className="button button-secondary" disabled={loading || !!pending} onClick={refresh}><RefreshCw size={16} className={loading ? "spin" : ""} />刷新状态</button></div>{success && <Notice tone="success">{success}</Notice>}{actionError && <Notice>{actionError}{needsCleanup && " 清理尚未完成，请在下方重试删除。"}</Notice>}{loadError && <Notice>{loadError}</Notice>}{loading && !resume && !lifecycle ? <div className="loading-card"><Spinner label="正在读取文件和生命周期状态" /></div> : !resume && !lifecycle ? <section className="panel unavailable-state"><FileText size={36} strokeWidth={1.5} /><h2>这份简历暂时无法查看</h2><p>检查当前账户与网络连接后，可以刷新状态或返回文件列表。</p><div><button className="button button-secondary" onClick={refresh}>重试</button><Link href="/resumes" className="button button-primary">返回我的简历<ArrowRight size={15} /></Link></div></section> : deleted ? <section className="panel deleted-state"><div className="deleted-icon"><CircleCheck size={35} strokeWidth={1.5} /></div><h2>这份简历已删除</h2><p>原件、解析文本及关联资料已完成清理。<br />最小生命周期记录保留用于确认删除与防止重复操作。</p><Link href="/resumes" className="button button-primary">返回我的简历<ArrowRight size={16} /></Link></section> : <div className="detail-grid"><div className="detail-main"><section className="panel file-overview"><div className="file-overview-top"><FileIcon large /><div><span className="file-category">简历原件</span><h2>{resume?.file_name || "文件状态记录"}</h2><p>{resume ? `PDF · ${formatDate(resume.created_at, true)} 上传` : "当前文件处于处理或清理阶段，元数据暂不可用。"}</p></div></div><div className="file-overview-actions"><button className="button button-primary" disabled={!canManage || !!pending} onClick={() => { void mutate("download"); }}>{pending === "download" ? <Spinner label="正在下载" /> : <><Download size={16} />下载原件</>}</button><button ref={renameButton} className="button button-secondary" disabled={!canManage || !!pending} onClick={() => { setNewName(resume?.file_name || ""); setNameError(null); setRenameOpen(true); }}><Pencil size={15} />修改文件名</button></div><div className="file-overview-note"><ShieldCheck size={16} /><span>文件通过账户授权后下载，原件内容不会因改名而修改。</span></div></section><section className="panel lifecycle-panel"><div className="section-heading"><div><h2>文件生命周期</h2><p>这里显示服务器实际返回的处理与清理状态。</p></div><span className={`badge ${lifecycle?.lifecycle_status === "active" && lifecycle.processing_status === "parsed" ? "badge-success" : needsCleanup ? "badge-warning" : "badge-neutral"}`}>{pending === "delete" ? "删除请求处理中" : lifecycle ? lifecycleText(lifecycle.lifecycle_status) : "状态未知"}</span></div>{lifecycle ? <><div className="lifecycle-steps"><div className={lifecycle.processing_status === "parsed" ? "complete" : ""}><span>{lifecycle.processing_status === "parsed" ? <Check size={15} /> : "1"}</span><div><strong>文件保存</strong><p>{lifecycle.processing_status === "parsed" || lifecycle.processing_status === "uploaded" ? "原始 PDF 已保存" : lifecycle.processing_status === "uploading" ? "服务器正在接收文件" : "文件状态待确认"}</p></div></div><div className={lifecycle.processing_status === "parsed" ? "complete" : ""}><span>{lifecycle.processing_status === "parsed" ? <Check size={15} /> : "2"}</span><div><strong>文本解析</strong><p>{processingText(lifecycle.processing_status)}</p></div></div><div className="future-step"><span>3</span><div><strong>AI 画像</strong><p>尚未开放，未生成画像</p></div></div></div><dl className="lifecycle-data"><div><dt>处理状态</dt><dd>{processingText(lifecycle.processing_status)}</dd></div><div><dt>文件状态</dt><dd>{lifecycleText(lifecycle.lifecycle_status)}</dd></div><div><dt>清理状态</dt><dd>{lifecycle.deletion_status ? lifecycleText(lifecycle.deletion_status) : "未发起清理"}</dd></div><div><dt>清理尝试次数</dt><dd>{lifecycle.attempt_count}</dd></div>{lifecycle.error_code && <div><dt>安全错误代码</dt><dd><code>{lifecycle.error_code}</code></dd></div>}</dl>{pollStopped && <Notice tone="info">处理仍未完成，自动刷新已暂停。请稍后点击“刷新状态”查看最新结果。</Notice>}{hidden && <div className="cleanup-warning"><AlertTriangle size={19} /><div><strong>{lifecycle.lifecycle_status === "delete_failed" ? "删除失败，清理尚未完成" : "文件正在删除"}</strong><p>文件已从普通列表隐藏。原件清理需由服务器确认完成，刷新页面不会丢失这条状态。</p></div></div>}</> : <p className="state-unknown">状态暂不可用，请使用上方“刷新状态”重新查询。</p>}</section></div><aside className="detail-side"><section className="panel detail-info"><span className="detail-info-icon"><FileText size={20} /></span><h3>文件与画像是两回事</h3><p>文本解析只读取 PDF 中的文字，并不表示经历已被 AI 识别或确认。</p><p>当前版本不会生成 AI 画像，也不会发起求职申请。</p></section><section className="panel danger-panel"><div><Trash2 size={18} /><h3>{needsCleanup ? "继续清理文件" : "删除这份简历"}</h3></div><p>{needsCleanup ? "这份文件的清理尚未完成。重试会继续同一条删除流程。" : "删除原件、解析文本及其关联资料。完成后无法撤销。"}</p><button ref={deleteButton} className="button button-danger-quiet" disabled={!!pending || (!canManage && !needsCleanup)} onClick={() => setDeleteOpen(true)}>{pending === "delete" ? <Spinner label="正在删除" /> : <><Trash2 size={15} />{needsCleanup ? "重试删除" : "删除简历"}</>}</button></section></aside></div>}<PrivacyNote /><Dialog.Root open={renameOpen} onOpenChange={(value) => { if (!busy.current) setRenameOpen(value); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content onOpenAutoFocus={(event) => { event.preventDefault(); document.getElementById("file_name")?.focus(); }} onCloseAutoFocus={(event) => { event.preventDefault(); renameButton.current?.focus(); }} className="dialog-content compact-dialog" onEscapeKeyDown={(event) => { if (pending) event.preventDefault(); }} onInteractOutside={(event) => { if (pending) event.preventDefault(); }}><div className="dialog-heading"><div><Dialog.Title>修改文件名</Dialog.Title><Dialog.Description>为原件取一个便于查找的名称，保留 .pdf 后缀。</Dialog.Description></div><Dialog.Close className="icon-button dialog-close" aria-label="关闭修改窗口" disabled={!!pending}><X size={19} /></Dialog.Close></div><form onSubmit={(event) => { event.preventDefault(); void mutate("rename"); }}><div className="form-field"><label htmlFor="file_name">文件名</label><input id="file_name" value={newName} onChange={(event) => setNewName(event.target.value)} disabled={!!pending} autoComplete="off" aria-invalid={!!nameError} aria-describedby={nameError ? "file-name-error" : "file-name-hint"} />{nameError ? <p className="field-error" id="file-name-error">{nameError}</p> : <p className="field-hint" id="file-name-hint">最多 255 个字符，不能包含文件路径。</p>}</div>{actionError && <Notice>{actionError}</Notice>}<div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={!!pending}>取消</Dialog.Close><button className="button button-primary" type="submit" disabled={!!pending}>{pending === "rename" ? <Spinner label="正在保存" /> : "保存修改"}</button></div></form></Dialog.Content></Dialog.Portal></Dialog.Root><Dialog.Root open={deleteOpen} onOpenChange={(value) => { if (!busy.current) setDeleteOpen(value); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content onCloseAutoFocus={(event) => { event.preventDefault(); deleteButton.current?.focus(); }} role="alertdialog" className="dialog-content compact-dialog" onEscapeKeyDown={(event) => { if (pending) event.preventDefault(); }} onInteractOutside={(event) => { if (pending) event.preventDefault(); }}><div className="delete-dialog-icon"><Trash2 size={25} /></div><Dialog.Title>{needsCleanup ? "继续删除这份简历？" : "确认删除这份简历？"}</Dialog.Title><Dialog.Description className="delete-description">{resume && <strong>{resume.file_name}</strong>}原始 PDF、解析文本及关联资料都会被清理。只有服务器确认完成后才会显示已删除，此操作无法撤销。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={!!pending}>保留文件</Dialog.Close><button className="button button-danger" disabled={!!pending} onClick={() => { void mutate("delete"); }}>{pending === "delete" ? <Spinner label="正在删除" /> : "确认删除"}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root></WorkspaceShell>;
}
