"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ListChecks, RefreshCw } from "lucide-react";
import { api, ApiError, formatError, type CareerProfile, type Job, type JobMatch, type MatchItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate, Notice, Spinner } from "@/components/ui";

const conclusions = { hard_conflict: "存在明确硬性条件冲突", verify_first: "建议先核实关键条件", consider: "可以进一步考虑" };
const statuses = { met: "明确满足", partial: "部分满足", unknown: "未发现证据", unmet: "明确不满足" };
const groups = { education: "学历与届别", skills: "技术技能", experience: "项目及实习", work_conditions: "工作条件", preferences: "用户求职偏好" };
const priorities = { must: "必须条件", preferred: "优先条件", uncertain: "待核实条件" };
const fieldNames: Record<string, string> = { school: "学校", degree: "学历", major: "专业", end_date: "毕业 / 结束时间", end_date_kind: "毕业时间类型", start_date: "开始时间", name: "名称", level: "掌握情况", description: "经历内容", role: "角色 / 岗位", organization: "单位", experience_type: "经历类型", locations: "意向城市", work_types: "工作类型", target_roles: "岗位方向", salary: "薪资偏好", weekly_days: "每周可实习天数", internship_months: "可实习月数" };
const sourceNames: Record<string, string> = { manual: "用户填写", confirmed: "用户确认", modified: "用户修订", user_modified: "用户修订", resume_confirmed: "用户确认的简历事实", resume: "用户确认的简历事实", resume_deleted: "来源简历已删除", preferences: "用户确认的求职偏好", preference: "用户确认的求职偏好" };
const valueNames: Record<string, string> = { actual: "实际毕业", expected: "预计毕业", internship: "实习", work: "工作", full_time: "全职", campus: "校招", true: "是", false: "否" };
const staleNames = { profile_changed: "正式画像已更新", job_changed: "职位或已审核条件已更新", rule_changed: "匹配规则已更新" };
const excludedNames = { pending_review: "尚未审核确认", stale_jd: "引用的 JD 已更新，需要重新审核" };

function display(value: unknown, field?: string): string {
  if (value == null) return "未知";
  if (field === "work_types" && typeof value === "string") {
    const labels: Record<string, string> = { internship: "实习", full_time: "全职", campus: "校招" };
    return value.split(/[、,，]/).map(part => labels[part.trim()] || part.trim()).join("、");
  }
  if (field === "salary" && typeof value === "string") {
    try {
      const salary: unknown = JSON.parse(value);
      if (salary && typeof salary === "object" && !Array.isArray(salary)) {
        const data = salary as Record<string, unknown>;
        const periods: Record<string, string> = { day: "天", month: "月", year: "年" };
        const taxes: Record<string, string> = { pre_tax: "税前", after_tax: "税后", unknown: "税费口径未明确" };
        const amount = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0;
        if (Object.keys(data).every(key => ["min", "max", "currency", "period", "tax"].includes(key)) && data.currency === "CNY" && typeof data.period === "string" && Object.hasOwn(periods, data.period) && typeof data.tax === "string" && Object.hasOwn(taxes, data.tax) && (data.min == null || amount(data.min)) && (data.max == null || amount(data.max)) && (amount(data.min) || amount(data.max)) && !(amount(data.min) && amount(data.max) && data.min > data.max)) {
          const range = amount(data.min) && amount(data.max) ? `${data.min}—${data.max}` : String(data.min ?? data.max);
          const unknown = data.min == null ? "，下限未填写" : data.max == null ? "，上限未填写" : "";
          return `${taxes[data.tax]} ${range} 元/${periods[data.period]}（人民币${unknown}）`;
        }
      }
    } catch { /* Unsupported snapshots keep their original evidence text. */ }
    return value;
  }
  if (typeof value === "string") return valueNames[value] || value;
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function ready(profile: CareerProfile): boolean {
  return !!(profile.education.length || profile.skills.length || profile.projects.length || profile.experiences.length || Object.values(profile.preferences || {}).some(value => Array.isArray(value) ? value.length : value != null));
}

function MatchCondition({ item }: { item: MatchItem }) {
  const jd = item.jd_evidence;
  const facts = item.profile_evidence || [];
  const verification = item.verification || [];
  return <article className={`match-condition match-status-${item.status}`} aria-label={`匹配条件：${item.content}`}>
    <div className="match-condition-heading"><strong>{item.content}</strong><div><span className="badge badge-neutral">{item.scope === "preference" ? "个人偏好" : priorities[item.priority]}</span><span className={`badge match-badge-${item.status}`}>{statuses[item.status]}</span></div></div>
    <p className="match-reason">{item.reason}</p>
    <details className="match-evidence"><summary>查看判断与证据</summary><div className="match-evidence-grid">
      <section><h4>正式画像事实</h4>{facts.length ? facts.map((fact, index) => <div className="match-fact" key={`${fact.entry_id}-${fact.field}-${index}`}><p><strong>{fieldNames[fact.field] || fact.field}：</strong>{display(fact.value, fact.field)}</p><p className="match-source">{sourceNames[fact.source_kind] || "已确认的正式资料"}{fact.entry_id && <span> · 条目 {fact.entry_id}</span>}</p>{fact.source_text && <blockquote>{fact.source_text}</blockquote>}</div>) : <p>正式画像中未发现可支持这项判断的事实。缺少记录不代表你不具备该能力。</p>}</section>
      <section><h4>{jd?.source_kind === "job_field" ? "已保存职位信息" : "JD 原文依据"}</h4>{jd ? <><blockquote>{jd.evidence_text || "用户手工确认的条件，未提供原文引文。"}</blockquote><p className="match-source">{jd.source_jd_version != null ? `JD v${jd.source_jd_version}` : "JD 版本未知"}{jd.line_start != null && ` · 第 ${jd.line_start}${jd.line_end !== jd.line_start ? `—${jd.line_end}` : ""} 行`}{jd.source_start != null && ` · 字符 ${jd.source_start}—${jd.source_end}`}</p></> : <p>个人求职偏好与已保存职位信息对比，不属于招聘方的硬性资格。</p>}</section>
    </div>{!!verification.length && <div className="match-verification"><h4>尚需核实</h4><ul>{verification.map((text, index) => <li key={index}>{text}</li>)}</ul></div>}<p className="match-source">规则版本：{item.rule_version}{item.requirement_id && ` · 条件 ${item.requirement_id}`}</p></details>
  </article>;
}

export function JobMatchPanel({ job, onReview, disabled = false }: { job: Job; onReview(): void; disabled?: boolean }) {
  const { user, status } = useAuth();
  const [profile, setProfile] = useState<CareerProfile | null>(null);
  const [result, setResult] = useState<JobMatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [history, setHistory] = useState<JobMatch[] | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [basisConflict, setBasisConflict] = useState(false);
  const action = useRef<AbortController | null>(null);
  const locked = useRef(false);
  const attempt = useRef<{ basis: string; key: string } | null>(null);
  useEffect(() => () => action.current?.abort(), [user?.id, status]);
  useEffect(() => {
    if (status !== "authenticated") return;
    const controller = new AbortController();
    async function load() {
      const outcomes = await Promise.allSettled([api.profile(controller.signal), api.latestMatch(job.id, controller.signal)]);
      if (controller.signal.aborted) return;
      const [profileOutcome, matchOutcome] = outcomes;
      if (profileOutcome.status === "fulfilled") setProfile(profileOutcome.value);
      if (matchOutcome.status === "fulfilled") setResult(matchOutcome.value);
      setBasisConflict(profileOutcome.status === "rejected" || matchOutcome.status === "rejected");
      setError(profileOutcome.status === "rejected" ? formatError(profileOutcome.reason) : matchOutcome.status === "rejected" ? formatError(matchOutcome.reason) : null);
      setHistory(null); setLoading(false);
    }
    void load();
    return () => controller.abort();
  }, [job.id, job.revision, reload, user?.id, status]);
  useEffect(() => {
    function refreshOnReturn() {
      if (document.visibilityState === "hidden" || loading || locked.current || historyBusy || disabled || status !== "authenticated") return;
      setLoading(true); setReload(value => value + 1);
    }
    window.addEventListener("focus", refreshOnReturn);
    document.addEventListener("visibilitychange", refreshOnReturn);
    return () => { window.removeEventListener("focus", refreshOnReturn); document.removeEventListener("visibilitychange", refreshOnReturn); };
  }, [loading, historyBusy, disabled, status]);

  async function calculate() {
    if (!profile || locked.current || disabled || loading || historyBusy) return;
    locked.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); action.current = controller;
    const rulesBasis = result?.stale_reasons.includes("rule_changed") ? `rule_changed:${result.id}` : "current_rules";
    const basis = `${user?.id}:${job.id}:${job.revision}:${profile.revision}:${rulesBasis}`;
    if (attempt.current?.basis !== basis) attempt.current = { basis, key: crypto.randomUUID() };
    try {
      const data = await api.createMatch(job.id, { profile_revision: profile.revision, job_revision: job.revision, idempotency_key: attempt.current.key }, controller.signal);
      if (!controller.signal.aborted) { setResult(data); setHistory(null); setBasisConflict(false); }
    } catch (cause) {
      if (!controller.signal.aborted) { setBasisConflict(true); setError(cause instanceof ApiError && cause.status === 409 ? `${formatError(cause)} 请刷新匹配状态并重新载入职位，核对最新画像与已审核条件后再计算。` : `匹配未完成。${formatError(cause)} 请先刷新匹配状态确认结果，再重试。`); }
    } finally { locked.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  async function loadHistory() {
    if (historyBusy || busy) return;
    const controller = new AbortController(); action.current = controller;
    setHistoryBusy(true); setHistoryError(null);
    try { const data = await api.matchHistory(job.id, controller.signal); if (!controller.signal.aborted) setHistory(data.items); }
    catch (cause) { if (!controller.signal.aborted) setHistoryError(formatError(cause)); }
    finally { if (!controller.signal.aborted) setHistoryBusy(false); }
  }
  const confirmed = job.requirements.filter(item => item.review_status === "confirmed" && !item.stale);
  const profileReady = profile && ready(profile);
  const localBasisChanged = !!result && (result.job_revision !== job.revision || (!!profile && result.profile_revision !== profile.revision));
  const stale = result?.stale || basisConflict || localBasisChanged;
  return <section className="panel match-panel" aria-label="岗位匹配">
    <div className="section-heading"><div className="profile-group-title"><span className="profile-section-icon"><ListChecks size={21} /></span><div><h2>岗位匹配</h2><p>基于本人确认的资料与已审核条件，逐项解释判断。</p></div></div><button className="text-button" disabled={loading || busy || historyBusy} onClick={() => { setLoading(true); setReload(value => value + 1); }}><RefreshCw size={14} />刷新匹配状态</button></div>
    <p className="match-boundary">匹配结果是辅助决策，不是录用概率。未发现证据不等于不具备能力；收藏和岗位选择始终由你决定。</p>
    {loading ? <div className="match-loading"><Spinner label="正在读取匹配依据" /></div> : <>
      {error && <Notice>{error}</Notice>}
      {!profileReady && !error && <Notice tone="info">还没有已确认的求职资料。请先到“我的资料”保存真实经历或求职偏好，未确认的 AI 候选不会参与匹配。<Link href="/profile" className="inline-link">完善我的资料</Link></Notice>}
      {!confirmed.length && <Notice tone="info">暂无当前 JD 的已审核条件。请先对照原文确认条件并保存，再开始匹配。<button className="text-button" disabled={disabled} onClick={onReview}>去审核 JD 条件</button></Notice>}
      {disabled && <Notice tone="info">请先保存或放弃职位修改，再使用已保存的条件匹配。</Notice>}
      {result ? <>
        {stale && <Notice tone="info"><strong>{result.stale || localBasisChanged ? "匹配依据已变更" : "匹配依据状态需要重新核实"}</strong>：{result.stale_reasons.map(reason => staleNames[reason]).join("、") || (localBasisChanged ? "已保存资料或职位版本已变化" : "本次计算未能确认当前依据")}。以下为历史判断，请核对资料后重新计算。</Notice>}
        <div className={`match-summary match-conclusion-${result.evaluation.conclusion}${stale ? " match-summary-stale" : ""}`}><p className="match-summary-eyebrow">{stale ? "历史综合建议" : "综合建议"} · {job.title}</p><h3>{conclusions[result.evaluation.conclusion]}</h3><ul>{result.evaluation.reasons.map((text, index) => <li key={index}>{text}</li>)}</ul><p className="match-source">计算时间：{formatDate(result.created_at, true)} · 画像 v{result.profile_revision} · 职位 v{result.job_revision} / JD v{result.jd_version} · 规则 {result.rule_version}</p><p className="match-version-state">{stale ? "依据版本已变化或尚未确认，不能作为最新判断" : "依据版本与当前保存内容一致"}</p></div>
        <p className="match-detail-intro">每项条件独立判断。个人偏好与招聘资格分别展示，优先条件未满足不会单独形成硬性冲突。</p>
        {(Object.keys(groups) as (keyof typeof groups)[]).map(group => {
          const items = result.evaluation.items.filter(item => item.group === group);
          return items.length ? <section key={group} className="match-group" aria-label={groups[group]}><h3>{groups[group]}<span>{items.length} 项</span></h3>{items.map((item, index) => <MatchCondition key={item.requirement_id || `${group}-${index}`} item={item} />)}</section> : null;
        })}
        {!!result.evaluation.excluded_requirements.length && <details className="match-excluded"><summary>未纳入正式匹配的条件（{result.evaluation.excluded_requirements.length} 项）</summary><p>以下条件尚未审核或依据已过期，不用于推导满足或不满足。</p><ul>{result.evaluation.excluded_requirements.map((item, index) => <li key={index}><strong>{item.content}</strong>：{excludedNames[item.reason]}</li>)}</ul></details>}
        <div className="match-next"><h3>下一步建议</h3><ul>{result.evaluation.next_steps.map((text, index) => <li key={index}>{text}</li>)}</ul></div>
      </> : !error && <div className="match-empty"><h3>先确认依据，再看匹配</h3><p>系统只读取服务器保存的正式画像和已审核条件。开始后可查看每项判断、双方证据，以及仍需核实的问题。</p></div>}
      <div className="match-actions"><button className="button button-primary" disabled={busy || historyBusy || disabled || !profileReady || !confirmed.length || !!error} onClick={() => { void calculate(); }}>{busy ? <Spinner label="正在逐项匹配" /> : result ? "重新计算匹配" : "开始匹配"}</button><Link href="/profile" className="button button-secondary">修改个人画像</Link><button className="text-button" disabled={busy || disabled} onClick={onReview}>审核 JD 条件</button></div>
      {result && <div className="match-history"><button className="text-button" disabled={historyBusy || busy} onClick={() => { void loadHistory(); }}>{historyBusy ? "正在读取历史…" : "查看最近匹配记录"}</button>{historyError && <Notice>{historyError}</Notice>}{history && <div><p className="match-source">最近 {history.length} 次独立依据的结果；相同依据的重复计算复用记录。</p><ol>{history.map(item => <li key={item.id}><strong>{conclusions[item.evaluation.conclusion]}</strong><span>{formatDate(item.created_at, true)} · 画像 v{item.profile_revision} / JD v{item.jd_version} · {item.stale ? "依据已变更" : "当前依据"}</span></li>)}</ol></div>}</div>}
    </>}
  </section>;
}
