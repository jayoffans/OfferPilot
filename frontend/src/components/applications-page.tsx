"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowUpRight, CalendarClock, ClipboardList, RefreshCw, Search } from "lucide-react";
import { api, type ApplicationList } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WorkspaceShell } from "@/components/workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { actionDue, actionStatuses, ApplicationBadge, applicationError, applicationStatuses, isTerminal } from "@/components/applications-ui";

const sorts = ["updated_desc", "created_desc", "submitted_desc", "next_action_asc"];
function readFilters(query: URLSearchParams) {
  const rawPage = Number(query.get("page") || "1");
  return { search: (query.get("q") || "").slice(0, 200), state: Object.hasOwn(applicationStatuses, query.get("status") || "") ? query.get("status") || "" : "", sort: sorts.includes(query.get("sort") || "") ? query.get("sort")! : "updated_desc", nextAction: ["pending", "completed", "cancelled", "none"].includes(query.get("next_action") || "") ? query.get("next_action")! : "", overdue: ["true", "false"].includes(query.get("overdue") || "") ? query.get("overdue")! : "", page: Number.isSafeInteger(rawPage) && rawPage >= 1 && rawPage <= 10000 ? rawPage : 1 };
}
function filterQuery(filters: ReturnType<typeof readFilters>) {
  const query = new URLSearchParams();
  if (filters.search) query.set("q", filters.search);
  if (filters.state) query.set("status", filters.state);
  if (filters.nextAction) query.set("next_action", filters.nextAction);
  if (filters.overdue) query.set("overdue", filters.overdue);
  query.set("sort", filters.sort); query.set("page", String(filters.page));
  return query;
}

export function ApplicationsPage() {
  const { status, user } = useAuth();
  const userId = user?.id;
  const [result, setResult] = useState<ApplicationList | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const urlQuery = useSearchParams().toString();
  const [filters, setFilters] = useState(() => readFilters(new URLSearchParams(urlQuery)));
  const { search, state, sort, nextAction, overdue, page } = filters;
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setFilters(readFilters(new URLSearchParams(urlQuery))), 0);
    return () => clearTimeout(timer);
  }, [urlQuery]);
  function change(values: Partial<typeof filters>) {
    const next = { ...filters, page: 1, ...values };
    setFilters(next);
    window.history.replaceState(null, "", `/applications?${filterQuery(next)}`);
  }
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true); setError(null);
      const query = new URLSearchParams({ sort, page: String(page), page_size: "12" });
      if (search.trim()) query.set("q", search.trim());
      if (state) query.set("status", state);
      if (nextAction) query.set("next_action", nextAction);
      if (overdue) query.set("overdue", overdue);
      void api.applications(query, controller.signal).then(data => { if (!controller.signal.aborted) { setResult(data); setOwner(userId); } }).catch(cause => { if (!controller.signal.aborted) setError(applicationError(cause)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, search ? 200 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [status, userId, search, state, sort, nextAction, overdue, page, refresh]);
  const visible = owner === userId ? result : null;
  const filtered = !!(search || state || nextAction || overdue);
  const returnTo = `/applications?${filterQuery(filters)}`;
  return <WorkspaceShell><PageHeading eyebrow="每一步进展，都由你确认" title="投递进度" description="记录真实申请、面试进展和下一步行动，让每个机会都有着落。" action={<Link className="button button-primary" href="/jobs">从职位创建申请<ArrowUpRight size={16} /></Link>} />
    <div className="list-helper"><ClipboardList size={17} /><span>收藏是关注，申请是决定。这里仅显示你主动创建的申请记录。</span></div>
    <section className="panel applications-panel" aria-label="申请列表"><div className="jobs-toolbar"><div className="search-input"><Search size={16} /><input aria-label="搜索申请" placeholder="搜索岗位、公司或城市" maxLength={200} value={search} onChange={event => { change({ search: event.target.value }); }} /></div><button className="icon-button" aria-label="刷新申请列表" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={17} /></button><div className="jobs-filters"><select aria-label="申请状态筛选" value={state} onChange={event => { change({ state: event.target.value }); }}><option value="">全部申请状态</option>{Object.entries(applicationStatuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><select aria-label="下一步行动筛选" value={nextAction} onChange={event => change({ nextAction: event.target.value })}><option value="">全部行动状态</option><option value="pending">有待完成行动</option><option value="completed">行动已完成</option><option value="cancelled">行动已取消</option><option value="none">没有下一步行动</option></select><select aria-label="逾期筛选" value={overdue} onChange={event => change({ overdue: event.target.value })}><option value="">全部到期情况</option><option value="true">仅已逾期</option><option value="false">未逾期（含无待办）</option></select><select aria-label="申请排序" value={sort} onChange={event => { change({ sort: event.target.value }); }}><option value="updated_desc">最近更新</option><option value="created_desc">最近创建</option><option value="submitted_desc">最近投递</option><option value="next_action_asc">行动截止日期</option></select></div></div>
      {error ? <div className="job-panel-body"><Notice>{error}</Notice><button className="button button-secondary" onClick={() => setRefresh(value => value + 1)}>重新加载申请</button></div> : loading || !visible ? <div className="loading-card"><Spinner label="正在读取申请记录" /></div> : visible.items.length ? <><div className="application-table-heading" aria-hidden="true"><span>岗位与公司</span><span>申请状态 / 实际投递</span><span>下一步行动</span><span>最近更新</span></div><div>{visible.items.map(item => <Link href={`/applications/${item.id}?returnTo=${encodeURIComponent(returnTo)}`} className="application-row" key={item.id}><div className="application-row-job"><strong>{item.job_title}</strong><span>{item.company || "公司待补充"} · {item.location || "地点未知"}</span><small>第 {item.attempt_number} 次申请</small></div><div className="application-row-state"><ApplicationBadge status={item.status} /><span>{item.submitted_at ? `${formatDate(item.submitted_at, true)} 投递` : "实际投递时间未知"}</span></div><div className="application-row-action">{item.next_action ? <><strong><CalendarClock size={14} />{item.next_action.title}</strong><span>{actionDue(item.next_action)}</span><span className={item.is_overdue ? "application-overdue" : ""}>{isTerminal(item.status) && item.next_action.status === "pending" ? "申请已结束，此行动不计入待办" : item.is_overdue ? "已逾期 · 待完成" : actionStatuses[item.next_action.status]}</span></> : <span className="muted">尚未安排下一步</span>}</div><div className="application-row-updated"><time dateTime={item.updated_at}>{formatDate(item.updated_at, true)}</time><ArrowUpRight size={17} /></div></Link>)}</div><div className="list-pagination"><span>共 {visible.total} 条申请 · 第 {visible.page} 页</span><div><button className="icon-button" aria-label="上一页申请" disabled={page <= 1} onClick={() => change({ page: page - 1 })}>‹</button><button className="icon-button" aria-label="下一页申请" disabled={page * visible.page_size >= visible.total} onClick={() => change({ page: page + 1 })}>›</button></div></div></> : <div className="jobs-empty"><span className="empty-icon"><ClipboardList size={30} /></span><h2>{filtered ? "没有找到符合筛选的申请" : "从一个认真考虑的机会开始"}</h2><p>{filtered ? "调整关键词或状态，再试一次。" : "打开已保存职位，点击“准备投递”，再记录真实进展。收藏职位不会自动创建申请。"}</p>{filtered ? <button className="button button-secondary" onClick={() => { change({ search: "", state: "", nextAction: "", overdue: "" }); }}>清除申请筛选</button> : <Link className="button button-primary" href="/jobs">查看已保存职位</Link>}</div>}
    </section><p className="job-bottom-note">日期默认北京时间。仅填写日期的行动没有具体提醒时间；本轮只记录应用内行动，不发送通知。</p>
  </WorkspaceShell>;
}
