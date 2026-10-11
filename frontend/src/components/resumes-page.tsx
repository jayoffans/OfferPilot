"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpDown, ChevronLeft, ChevronRight, FileText, RefreshCw, Search, Upload, X } from "lucide-react";
import { WorkspaceShell } from "@/components/workspace-shell";
import { useResumeList } from "@/components/use-resume-list";
import { UploadDialog } from "@/components/upload-dialog";
import { Notice, PageHeading, PrivacyNote, ResumeRow, Spinner } from "@/components/ui";

const PAGE_SIZE = 8;

export function ResumesPage() {
  const { resumes, loading, error, refresh } = useResumeList();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => {
    const result = resumes.filter((resume) => resume.file_name.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()));
    return result.sort((a, b) => sort === "name" ? a.file_name.localeCompare(b.file_name, "zh-CN") : sort === "oldest" ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at));
  }, [resumes, query, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(page, pages);
  const visible = filtered.slice((activePage - 1) * PAGE_SIZE, activePage * PAGE_SIZE);

  return <WorkspaceShell><PageHeading eyebrow="你的个人资料库" title="我的简历" description="在一个地方保存、查看和管理你的简历原件。" action={<button className="button button-primary" onClick={() => setUploadOpen(true)}><Upload size={17} />上传简历</button>} /><div className="list-helper"><FileText size={18} /><p>支持文本型 PDF，最大 10 MiB，不超过 50 页。上传完成代表文件已保存、文本已解析。</p></div><section className="panel resume-list-panel"><div className="list-toolbar"><div className="search-input"><Search size={18} aria-hidden="true" /><input aria-label="搜索简历文件名" placeholder="搜索文件名…" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} />{query && <button className="icon-button" onClick={() => { setQuery(""); setPage(1); }} aria-label="清除搜索"><X size={15} /></button>}</div><div className="list-toolbar-actions"><div className="sort-select"><ArrowUpDown size={15} aria-hidden="true" /><select aria-label="简历排序" value={sort} onChange={(event) => { setSort(event.target.value); setPage(1); }}><option value="newest">最新上传</option><option value="oldest">最早上传</option><option value="name">文件名称</option></select></div><button className="icon-button refresh-button" aria-label="刷新简历列表" disabled={loading} onClick={() => { void refresh(); }}><RefreshCw size={17} className={loading ? "spin" : ""} /></button></div></div>{error ? <div className="list-state"><Notice>{error}</Notice><button className="button button-secondary" onClick={() => { void refresh(); }}>重新加载</button></div> : loading ? <div className="list-state"><Spinner label="正在读取简历列表" /></div> : resumes.length === 0 ? <div className="list-state empty-state"><div className="empty-icon"><FileText size={32} strokeWidth={1.5} /></div><h2>准备好你的第一份简历</h2><p>上传文本型 PDF，即可保存原件并查看文件状态。<br />简历文件只在你的账户下可见。</p><button className="button button-primary" onClick={() => setUploadOpen(true)}><Upload size={16} />上传我的第一份简历</button><span className="empty-format-note">PDF · 最大 10 MiB · 不超过 50 页</span></div> : filtered.length === 0 ? <div className="list-state empty-state"><Search size={30} strokeWidth={1.5} /><h2>没有找到对应的简历</h2><p>试试其他关键词，或清除搜索查看全部文件。</p><button className="button button-secondary" onClick={() => { setQuery(""); setPage(1); }}>清除搜索</button></div> : <><div className="list-column-heading"><span>文件名称</span><span>文件状态</span></div><div className="resume-rows">{visible.map((resume) => <ResumeRow key={resume.id} resume={resume} />)}</div><div className="list-pagination"><span>{query ? `找到 ${filtered.length} 份简历` : `共 ${resumes.length} 份简历`}<span className="pagination-range"> · {Math.min((activePage - 1) * PAGE_SIZE + 1, filtered.length)}–{Math.min(activePage * PAGE_SIZE, filtered.length)}</span></span><div><button className="icon-button" aria-label="上一页" disabled={activePage <= 1} onClick={() => setPage(activePage - 1)}><ChevronLeft size={17} /></button><span>{activePage} / {pages}</span><button className="icon-button" aria-label="下一页" disabled={activePage >= pages} onClick={() => setPage(activePage + 1)}><ChevronRight size={17} /></button></div></div></>}</section><div className="list-bottom-note"><PrivacyNote /><Link href="/" className="subtle-link">返回工作台</Link></div><UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} /></WorkspaceShell>;
}
