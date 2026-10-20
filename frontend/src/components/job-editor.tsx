"use client";

import { Check, Plus, Trash2 } from "lucide-react";
import type { Job, JobCreate, JobRequirementInput } from "@/lib/api";
import { priorities, requirementTypes, safeJobLink, workTypes } from "@/components/jobs-ui";

export type JobDraft = Pick<JobCreate, "title" | "company" | "location" | "salary" | "work_type" | "source_url" | "jd_text"> & { requirements: JobRequirementInput[] };
export const blankJob: JobDraft = { title: "", company: null, location: null, salary: null, work_type: "unknown", source_url: null, jd_text: "", requirements: [] };

export function requirementInput(item: JobRequirementInput): JobRequirementInput {
  return { ...(item.id ? { id: item.id } : {}), type: item.type, content: item.content, priority: item.priority, review_status: item.review_status || "pending", evidence_text: item.evidence_text || null, source_start: item.source_start ?? null, source_jd_version: item.source_jd_version ?? null };
}
export function jobDraft(job: Job): JobDraft {
  return { title: job.title, company: job.company, location: job.location, salary: job.salary, work_type: job.work_type, source_url: job.source_url, jd_text: job.jd_text, requirements: job.requirements.map(requirementInput) };
}
export function validateJob(draft: JobDraft): string | null {
  if (!draft.title.trim()) return "请填写岗位名称，其余基本信息可以稍后补充。";
  if (draft.source_url && !safeJobLink(draft.source_url)) return "招聘链接需要是没有登录凭据的 HTTP 或 HTTPS 地址。";
  if (draft.requirements.some(item => !item.content.trim())) return "有条件尚未填写内容，请补充或删除空条件后保存。";
  return null;
}

// API offsets count Unicode characters, not JavaScript UTF-16 code units.
export function evidenceStart(jd: string, quote: string | null, previous?: number | null): number | null {
  if (!quote) return null;
  const characters = Array.from(jd);
  if (previous != null && characters.slice(previous, previous + Array.from(quote).length).join("") === quote) return previous;
  const first = jd.indexOf(quote);
  if (first < 0 || jd.indexOf(quote, first + 1) >= 0) return null;
  return Array.from(jd.slice(0, first)).length;
}

export function JobBasicFields({ draft, disabled, onChange }: { draft: JobDraft; disabled: boolean; onChange(key: keyof JobDraft, value: string | null): void }) {
  return <div className="profile-fields job-basic-fields">
    {([{ key: "title", label: "岗位名称", placeholder: "例如：后端开发实习生", required: true }, { key: "company", label: "公司名称", placeholder: "可稍后补充" }, { key: "location", label: "工作城市", placeholder: "例如：上海，未写明则留空" }, { key: "salary", label: "薪资范围", placeholder: "例如：200–300 元 / 天" }] as const).map(field => <div className="form-field" key={field.key}><label htmlFor={`job-${field.key}`}>{field.label}</label><input id={`job-${field.key}`} value={draft[field.key] || ""} placeholder={field.placeholder} maxLength={200} required={"required" in field} disabled={disabled} onChange={event => onChange(field.key, field.key === "title" ? event.target.value : event.target.value || null)} /></div>)}
    <div className="form-field"><label htmlFor="job-work-type">工作类型</label><select id="job-work-type" value={draft.work_type || "unknown"} disabled={disabled} onChange={event => onChange("work_type", event.target.value)}>{Object.entries(workTypes).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></div>
    <div className="form-field"><label htmlFor="job-source-url">原始招聘链接</label><input id="job-source-url" type="url" value={draft.source_url || ""} placeholder="https://…（仅保存链接）" maxLength={2048} disabled={disabled} onChange={event => onChange("source_url", event.target.value || null)} /><p className="field-hint">仅保存和展示，不自动访问招聘网站。</p></div>
  </div>;
}

export function JobJdField({ value, disabled, onChange }: { value: string; disabled: boolean; onChange(value: string): void }) {
  return <div className="form-field job-jd-field"><label htmlFor="job-jd">招聘 JD</label><textarea id="job-jd" value={value} disabled={disabled} maxLength={30000} rows={12} placeholder={"在这里粘贴招聘岗位的完整文字，例如：\n岗位职责…\n任职要求…\n加分项…"} onChange={event => onChange(event.target.value)} /><div className="job-field-footer"><span>原文按文本保存，信息不完整时保持未知。</span><span>{value.length.toLocaleString("zh-CN")} / 30,000</span></div></div>;
}

export function RequirementEditor({ items, job, jdText, jdVersion, disabled, reviewBlocked = false, onChange }: { items: JobRequirementInput[]; job: Job | null; jdText: string; jdVersion: number; disabled: boolean; reviewBlocked?: boolean; onChange(items: JobRequirementInput[]): void }) {
  function patch(index: number, value: Partial<JobRequirementInput>, reset = true) {
    onChange(items.map((item, row) => row === index ? { ...item, ...value, ...(reset ? { review_status: "pending" as const } : {}) } : item));
  }
  function rebind(index: number) {
    const item = items[index];
    const position = evidenceStart(jdText, item.evidence_text || null, item.source_start);
    patch(index, { source_jd_version: jdVersion, source_start: position });
  }
  return <div className="job-requirement-editor"><div className="job-review-heading"><div><h2>结构化条件</h2><p>核对原文、内容和分类后逐项确认；未完成的条件也能保存。</p></div><button type="button" className="button button-secondary" disabled={disabled || items.length >= 100} onClick={() => onChange([...items, { type: "other", content: "", priority: "uncertain", review_status: "pending", evidence_text: null, source_start: null, source_jd_version: jdVersion }])}><Plus size={16} />补充条件</button></div>
    {!items.length && <div className="job-no-requirements"><h3>还没有结构化条件</h3><p>可点击“免费规则整理”，或手动补充。规则可能漏掉信息，请对照原文检查。</p></div>}
    {items.map((item, index) => {
      const original = job?.requirements.find(saved => saved.id === item.id);
      const stale = item.source_jd_version != null && item.source_jd_version !== jdVersion;
      const evidenceMissing = !!item.evidence_text && !jdText.includes(item.evidence_text);
      const ambiguousEvidence = !!item.evidence_text && jdText.includes(item.evidence_text) && evidenceStart(jdText, item.evidence_text, item.source_start) === null;
      const confirmed = item.review_status === "confirmed" && !stale;
      const prefix = `requirement-${index}`;
      return <article className={`job-requirement-card${confirmed ? " job-requirement-confirmed" : ""}`} key={item.id || `new-${index}`} aria-label={`条件第 ${index + 1} 项`} data-requirement-id={item.id || `new-${index}`}><div className="job-requirement-heading"><strong>条件 {index + 1}</strong><div><span className={`badge ${confirmed ? "badge-success" : "badge-neutral"}`}>{stale ? "JD 已更新 · 需重审" : confirmed ? "已确认" : "待审核"}</span><button type="button" className="icon-button" aria-label="删除条件" disabled={disabled} onClick={() => onChange(items.filter((_item, row) => row !== index))}><Trash2 size={16} /></button></div></div><div className="profile-fields"><div className="form-field"><label htmlFor={`${prefix}-type`}>条件类型</label><select id={`${prefix}-type`} disabled={disabled} value={item.type} onChange={event => patch(index, { type: event.target.value as JobRequirementInput["type"] })}>{Object.entries(requirementTypes).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></div><div className="form-field"><label htmlFor={`${prefix}-priority`}>条件分类</label><select id={`${prefix}-priority`} value={item.priority} disabled={disabled} onChange={event => patch(index, { priority: event.target.value as JobRequirementInput["priority"] })}>{Object.entries(priorities).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></div><div className="form-field profile-field-wide"><label htmlFor={`${prefix}-content`}>条件内容</label><textarea id={`${prefix}-content`} value={item.content} disabled={disabled} rows={2} maxLength={2000} onChange={event => patch(index, { content: event.target.value })} /></div><div className="form-field profile-field-wide"><label htmlFor={`${prefix}-evidence`}>JD 原文证据</label><textarea id={`${prefix}-evidence`} value={item.evidence_text || ""} disabled={disabled} rows={2} maxLength={3000} placeholder="粘贴当前 JD 的原句；原文未说明可留空" onChange={event => { const quote = event.target.value || null; const position = evidenceStart(jdText, quote, item.evidence_text === quote ? item.source_start : null); patch(index, { evidence_text: quote, source_start: position, source_jd_version: jdVersion }); }} /><p className="field-hint">引用仅证明原文中有这段文字；请自行核实条件含义，不扩充专业范围或把优先升级为必须。</p></div></div>
        {item.evidence_text && <div className="form-field job-source-position"><label htmlFor={`${prefix}-position`}>证据字符起点</label><input id={`${prefix}-position`} type="number" min={0} max={30000} step={1} value={item.source_start ?? ""} disabled={disabled} onChange={event => patch(index, { source_start: event.target.value === "" ? null : Number(event.target.value), source_jd_version: jdVersion })} /><p className="field-hint">从 0 开始计数。原句重复出现时请指定位置；系统不会替你猜测。</p></div>}
        {ambiguousEvidence && <p className="job-review-warning">这段引文出现多次或起点未对准，请指定正确的证据字符起点再确认。</p>}
        {stale && <div className="job-review-warning"><p>此条件来自 JD v{item.source_jd_version}，需对照当前 v{jdVersion} 重新绑定并审核。</p><button type="button" className="text-button" disabled={disabled || evidenceMissing || ambiguousEvidence} onClick={() => rebind(index)}>重新绑定当前 JD</button>{evidenceMissing && <p>旧引文已不在当前 JD 中，请修改或清空证据再重新绑定。</p>}</div>}
        {!stale && evidenceMissing && <p className="job-review-warning">未在当前 JD 中找到这段引文，请复制准确原句或清空后作为手动条件审核。</p>}
        {original?.line_start && <p className="job-evidence-location">已保存来源：JD v{original.source_jd_version} · 第 {original.line_start}{original.line_end !== original.line_start ? `–${original.line_end}` : ""} 行 · 字符 {original.source_start}–{original.source_end}</p>}
        <div className="job-requirement-actions"><span>{original?.source_kind === "rule" ? "规则整理，需人工核对" : "由你填写或修订"}{!item.evidence_text && " · 未提供原文证据"}</span><button type="button" className={`button ${confirmed ? "button-secondary" : "button-primary"}`} disabled={disabled || reviewBlocked || stale || evidenceMissing || ambiguousEvidence || !item.content.trim()} onClick={() => patch(index, { review_status: confirmed ? "pending" : "confirmed" }, false)}><Check size={15} />{confirmed ? "撤销确认" : "确认条件"}</button></div></article>;
    })}
  </div>;
}
