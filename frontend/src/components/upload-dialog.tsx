"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Check, FileUp, Upload, X } from "lucide-react";
import { api, ApiError, formatError } from "@/lib/api";
import { FileIcon, Notice, PrivacyNote, recoveryPath, Spinner, validPdfName } from "@/components/ui";

function UploadDialogForm({ open, onOpenChange }: { open: boolean; onOpenChange: (value: boolean) => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<string | null>(null);
  const [unknownResult, setUnknownResult] = useState(false);
  const [progress, setProgress] = useState(0);
  const [pending, setPending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const request = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const priorFocus = useRef<HTMLElement | null>(typeof document === "undefined" ? null : document.activeElement as HTMLElement);
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => () => { request.current?.abort(); if (priorFocus.current?.isConnected) priorFocus.current.focus(); }, []);

  function selectFile(selected: File | undefined) {
    if (!selected || busy.current || unknownResult) return;
    setRecovery(null); setProgress(0);
    const nameError = validPdfName(selected.name);
    if (nameError) { setError(nameError); setFile(null); return; }
    if (!selected.size || selected.size > 10 * 1024 * 1024) { setError("请选择非空且不超过 10 MiB 的 PDF 文件。"); setFile(null); return; }
    if (selected.type && selected.type !== "application/pdf") { setError("目前仅支持 PDF 文件，请选择正确格式。"); setFile(null); return; }
    setFile(selected); setError(null);
  }

  async function upload() {
    if (!file || busy.current || unknownResult) return;
    busy.current = true; setPending(true); setError(null); setRecovery(null);
    const controller = new AbortController(); request.current = controller;
    try {
      const result = await api.upload(file, (value) => { if (!controller.signal.aborted) setProgress(value); }, controller.signal);
      if (!controller.signal.aborted) { onOpenChange(false); router.push(`/resumes/${result.id}`); }
    } catch (cause) {
      if (!controller.signal.aborted) { setError(formatError(cause)); if (cause instanceof ApiError) { setRecovery(recoveryPath(cause.location)); setUnknownResult(["network_error", "request_timeout", "upload_result_unknown", "backend_timeout", "backend_unavailable"].includes(cause.code)); } }
    } finally { busy.current = false; if (!controller.signal.aborted) setPending(false); }
  }

  return <Dialog.Root open={open} onOpenChange={(value) => { if (!busy.current) onOpenChange(value); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content upload-dialog" onEscapeKeyDown={(event) => { if (pending) event.preventDefault(); }} onInteractOutside={(event) => { if (pending) event.preventDefault(); }}><div className="dialog-heading"><div className="dialog-title-icon"><FileUp size={21} /></div><div><Dialog.Title>上传一份简历</Dialog.Title><Dialog.Description>保存 PDF 原件，并读取其中的可选文本。</Dialog.Description></div><Dialog.Close className="icon-button dialog-close" aria-label="关闭上传窗口" disabled={pending}><X size={19} /></Dialog.Close></div><div className={`upload-dropzone${dragging ? " dragging" : ""}${pending ? " disabled" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!pending) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length > 1) { setError("每次请选择一份简历上传。"); return; } selectFile(event.dataTransfer.files[0]); }}><input ref={fileInput} id="resume-file" className="upload-file-input" type="file" accept=".pdf,application/pdf" aria-label="选择 PDF 简历" disabled={pending || unknownResult} onChange={(event) => selectFile(event.target.files?.[0])} /><div className="upload-cloud"><Upload size={27} strokeWidth={1.6} /></div><strong>{file ? "已选择文件，可以开始上传" : "将 PDF 拖到这里"}</strong><span>或<button type="button" className="text-button" disabled={pending || unknownResult} onClick={() => fileInput.current?.click()}>选择本地文件</button></span><p>PDF · 最大 10 MiB · 不超过 50 页</p></div>{file && <div className="selected-file"><FileIcon /><div><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MiB · PDF</span></div>{!pending && <button className="icon-button" onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ""; }} aria-label="移除所选文件"><X size={17} /></button>}</div>}{pending && <div className="upload-progress" role="status" aria-live="polite"><div><span>{progress >= 100 ? "文件已发送，正在保存并读取文本…" : "正在发送文件…"}</span><strong>{Math.round(progress)}%</strong></div><div className="progress-track" role="progressbar" aria-label="文件传输进度" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${progress}%` }} /></div><p>传输完成后仍需等待服务器确认，请保持页面打开。</p></div>}{error && <Notice>{error}{unknownResult && !recovery && <Link className="inline-link" href="/resumes" onClick={() => { onOpenChange(false); router.refresh(); }}>先查看简历列表，确认上传结果<ArrowRight size={14} /></Link>}{recovery && <Link className="inline-link" href={recovery} onClick={() => onOpenChange(false)}>查看文件状态，确认后再操作<ArrowRight size={14} /></Link>}</Notice>}<div className="upload-requirements"><Check size={16} /><p>请使用可选中、可复制文字的 PDF。扫描图片、加密文件或无文本的 PDF 暂不支持。</p></div><div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={pending}>取消</Dialog.Close><button className="button button-primary" onClick={() => { void upload(); }} disabled={!file || pending || !!recovery || unknownResult}>{pending ? <Spinner label="正在处理" /> : <><Upload size={16} />上传简历</>}</button></div><PrivacyNote /></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function UploadDialog(props: { open: boolean; onOpenChange: (value: boolean) => void }) {
  return props.open ? <UploadDialogForm {...props} /> : null;
}
