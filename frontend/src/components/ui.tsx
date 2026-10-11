"use client";

import Link from "next/link";
import { AlertCircle, ArrowUpRight, FileText, LoaderCircle, Navigation, ShieldCheck } from "lucide-react";
import type { Resume } from "@/lib/api";

export function Brand({ linked = true }: { linked?: boolean }) {
  const content = <><Navigation size={25} className="brand-mark" aria-hidden="true" strokeWidth={2.4} /><span>Offer<span className="brand-light">Pilot</span></span></>;
  return linked ? <Link href="/" className="brand" aria-label="OfferPilot 工作台">{content}</Link> : <div className="brand">{content}</div>;
}

export function Spinner({ label = "正在加载" }: { label?: string }) {
  return <span className="spinner-label" role="status"><LoaderCircle className="spin" size={18} aria-hidden="true" />{label}</span>;
}

export function Notice({ children, tone = "error" }: { children: React.ReactNode; tone?: "error" | "success" | "info" }) {
  return <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}><AlertCircle size={18} aria-hidden="true" /><div>{children}</div></div>;
}

export function PageHeading({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p className="page-description">{description}</p></div>{action && <div className="page-action">{action}</div>}</div>;
}

export function FileIcon({ large = false }: { large?: boolean }) {
  return <span className={`file-icon${large ? " file-icon-large" : ""}`} aria-hidden="true"><FileText size={large ? 32 : 23} strokeWidth={1.7} />{large && <span>PDF</span>}</span>;
}

export function formatDate(value: string, includeTime = false) {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", ...(includeTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}) }).format(date);
}

export function ResumeRow({ resume }: { resume: Resume }) {
  return <Link href={`/resumes/${resume.id}`} className="resume-row"><FileIcon /><div className="resume-row-name"><strong>{resume.file_name}</strong><span>PDF · {formatDate(resume.created_at)} 上传</span></div><span className="badge badge-success"><span className="status-dot" />文本已解析</span><ArrowUpRight className="row-arrow" size={18} aria-hidden="true" /></Link>;
}

export function PrivacyNote() {
  return <p className="privacy-note"><ShieldCheck size={15} aria-hidden="true" />简历由你的账户单独管理，原件下载需要登录。</p>;
}

export function validPdfName(name: string): string | null {
  if (!name.trim()) return "请输入文件名。";
  if (Array.from(name).length > 255) return "文件名不能超过 255 个字符。";
  if (!/\.pdf$/i.test(name.trim())) return "文件名需要以 .pdf 结尾。";
  if (/[/\\\x00-\x1f\x7f]/.test(name)) return "文件名不能包含路径分隔符或控制字符。";
  return null;
}

export function recoveryPath(location: string | null | undefined) {
  if (!location) return null;
  const match = /^\/api\/v1\/resumes\/([0-9a-f-]{36})\/lifecycle$/i.exec(location);
  return match ? `/resumes/${match[1]}` : null;
}
