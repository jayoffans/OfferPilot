"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, BriefcaseBusiness, Plus, RefreshCw, Search, Star } from "lucide-react";
import { api, type JobList } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WorkspaceShell } from "@/components/workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { jobError, reviewLabel, workTypes } from "@/components/jobs-ui";

export function JobsPage() {
  const { status, user } = useAuth();
  const [result, setResult] = useState<JobList | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [workType, setWorkType] = useState("");
  const [favorite, setFavorite] = useState("");
  const [review, setReview] = useState("");
  const [sort, setSort] = useState("updated_desc");
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const userId = user?.id;

  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true); setError(null);
      const query = new URLSearchParams({ sort, page: String(page), page_size: "12" });
      if (search.trim()) query.set("q", search.trim());
      if (workType) query.set("work_type", workType);
      if (favorite) query.set("is_favorite", favorite);
      if (review) query.set("review_status", review);
      void api.jobs(query, controller.signal).then(data => { if (!controller.signal.aborted) { setResult(data); setOwner(userId); } }).catch(cause => { if (!controller.signal.aborted) setError(jobError(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, search ? 200 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [status, userId, search, workType, favorite, review, sort, page, refresh]);

  function filter(setter: (value: string) => void, value: string) { setter(value); setPage(1); }
  const filtered = !!(search || workType || favorite || review);
  const visible = owner === userId ? result : null;
  return <WorkspaceShell><PageHeading eyebrow="保存机会，逐项看清要求" title="职位" description="粘贴招聘 JD 或手工创建，保留原文与条件审核进度。" action={<Link className="button button-primary" href="/jobs/new"><Plus size={17} />添加职位</Link>} />
    <div className="list-helper"><BriefcaseBusiness size={17} /><span>职位仅由你的账户管理。规则整理结果需要你审核后才能作为可靠依据。</span></div>
    <section className="panel jobs-list-panel" aria-label="已保存职位"><div className="jobs-toolbar"><div className="search-input"><Search size={16} /><input aria-label="搜索职位" placeholder="搜索岗位、公司或城市" value={search} maxLength={200} onChange={event => filter(setSearch, event.target.value)} /></div><button className="icon-button" aria-label="刷新职位列表" onClick={() => setRefresh(value => value + 1)} disabled={loading}><RefreshCw size={17} /></button><div className="jobs-filters"><select aria-label="工作类型筛选" value={workType} onChange={event => filter(setWorkType, event.target.value)}><option value="">全部类型</option>{Object.entries(workTypes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><select aria-label="收藏筛选" value={favorite} onChange={event => filter(setFavorite, event.target.value)}><option value="">全部职位</option><option value="true">已收藏</option><option value="false">未收藏</option></select><select aria-label="审核状态筛选" value={review} onChange={event => filter(setReview, event.target.value)}><option value="">全部审核状态</option><option value="pending">待审核</option><option value="reviewed">已审核</option></select><select aria-label="职位排序" value={sort} onChange={event => filter(setSort, event.target.value)}><option value="updated_desc">最近更新</option><option value="created_desc">最近添加</option><option value="title_asc">岗位名称</option></select></div></div>
      {error ? <div className="job-panel-body"><Notice>{error}</Notice><button className="button button-secondary" onClick={() => setRefresh(value => value + 1)}>重新加载</button></div> : loading || !visible ? <div className="loading-card"><Spinner label="正在读取你的职位" /></div> : visible.items.length ? <><div className="jobs-rows">{visible.items.map(job => <Link href={`/jobs/${job.id}`} className="job-row" key={job.id}><span className="job-icon"><BriefcaseBusiness size={22} /></span><div className="job-row-main"><strong>{job.title}{job.is_favorite && <Star size={14} className="job-star" aria-label="已收藏" fill="currentColor" />}</strong><span>{job.company || "公司待补充"} · {job.location || "地点未知"}</span><div className="job-row-tags"><span>{workTypes[job.work_type]}</span><span>{job.salary || "薪资未知"}</span></div></div><div className="job-row-state"><span className={`badge ${job.review_summary.pending || job.review_summary.stale ? "badge-neutral" : "badge-success"}`}>{reviewLabel(job.review_summary)}</span><time dateTime={job.updated_at}>{formatDate(job.updated_at, true)} 更新</time></div><ArrowUpRight size={17} className="row-arrow" /></Link>)}</div><div className="list-pagination"><span>共 {visible.total} 个职位 · 第 {visible.page} 页</span><div><button className="icon-button" aria-label="上一页职位" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>‹</button><button className="icon-button" aria-label="下一页职位" disabled={page * visible.page_size >= visible.total} onClick={() => setPage(value => value + 1)}>›</button></div></div></> : <div className="jobs-empty"><span className="empty-icon"><BriefcaseBusiness size={30} /></span><h2>{filtered ? "没有找到符合筛选的职位" : "保存你的第一个职位"}</h2><p>{filtered ? "调整关键词或筛选条件，再试一次。" : "复制招聘信息，先保存，再逐项审核要求。基本信息也可以稍后补充。"}</p>{filtered ? <button className="button button-secondary" onClick={() => { setSearch(""); setWorkType(""); setFavorite(""); setReview(""); setPage(1); }}>清除筛选</button> : <Link className="button button-primary" href="/jobs/new"><Plus size={16} />添加职位</Link>}</div>}
    </section><p className="job-bottom-note">打开职位查看匹配分析，也可以主动创建申请；收藏职位不会自动标记为已投递。</p></WorkspaceShell>;
}
