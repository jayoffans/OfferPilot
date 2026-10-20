"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Pencil, X } from "lucide-react";
import type { ProfileCandidate, ProfileReview } from "@/lib/api";
import { Notice, Spinner } from "@/components/ui";
import { entrySummary, FieldEditor, fields, groupNames, validateDraft, type ProfileEntryInput, type ProfileGroup } from "@/components/profile-editor";
import { CandidateExtractionDetails } from "@/components/candidate-extraction-details";

const candidateGroup: Record<ProfileCandidate["category"], ProfileGroup> = { education: "education", skill: "skills", project: "projects", experience: "experiences" };
const statusNames: Record<ProfileCandidate["status"], string> = { pending: "待确认", confirmed: "已确认", modified: "已修改", rejected: "已拒绝" };
const evidenceNames: Record<ProfileCandidate["evidence_status"], string> = { supported: "原文支持，仍需你确认", unsupported: "缺少可靠依据", needs_review: "原文需要复核" };

function ReviewedEvidence({ candidate }: { candidate: ProfileCandidate }) {
  const group = candidateGroup[candidate.category];
  return <details className="profile-source"><summary>查看审核来源与证据</summary><div className="profile-source-body"><p>原始候选依据：{evidenceNames[candidate.evidence_status]}。审核由你执行，不表示系统已核验经历真实性。</p><Link href={`/resumes/${candidate.resume_id}`} className="inline-link">查看来源简历</Link>{candidate.evidence.map((evidence, index) => <div className="evidence-snippet" key={`${evidence.field}-${index}`}><strong>{fields[group].find(field => field.key === evidence.field)?.label || "参考片段"}</strong><blockquote>{evidence.source_text}</blockquote><span>{candidate.evidence_status === "supported" ? "原始候选的文字支持，仅供回顾审核依据" : "候选引用（未获可靠原文支持）"}</span></div>)}{!candidate.evidence.length && <p>原始候选没有可靠的原文证据。</p>}</div></details>;
}

export function ProfileCandidates({ candidates, disabled, reviewing, error, onReview, onDraftChange }: { candidates: ProfileCandidate[]; disabled: boolean; reviewing: string | null; error: string | null; onReview(candidate: ProfileCandidate, action: ProfileReview["action"], value?: ProfileReview["value"]): Promise<boolean>; onDraftChange(dirty: boolean): void }) {
  const [editing, setEditing] = useState<ProfileCandidate | null>(null);
  const [value, setValue] = useState<ProfileEntryInput | null>(null);
  const [verified, setVerified] = useState(false);
  const [issue, setIssue] = useState<string | null>(null);
  const pending = candidates.filter(candidate => candidate.status === "pending");
  const reviewed = candidates.filter(candidate => candidate.status !== "pending");

  useEffect(() => {
    onDraftChange(!!editing && (verified || JSON.stringify(value) !== JSON.stringify(editing.value)));
  }, [editing, value, verified, onDraftChange]);

  async function modify() {
    if (!editing || !value || !verified || reviewing) return;
    const group = candidateGroup[editing.category];
    const draft = { education: [], skills: [], projects: [], experiences: [], [group]: [value] };
    const problem = validateDraft(draft);
    setIssue(problem);
    if (problem) return;
    if (await onReview(editing, "modify", value as ProfileReview["value"])) setEditing(null);
  }

  return <section className="panel profile-candidates" aria-labelledby="candidate-heading"><div className="section-heading"><div><h2 id="candidate-heading">待审核事实</h2><p>候选与正式资料分开保存，审核前不会进入正式画像。</p></div><span className={`badge ${pending.length ? "badge-warning" : "badge-neutral"}`}>{pending.length} 项待确认</span></div>{!pending.length ? <div className="profile-candidate-empty"><Check size={21} /><div><strong>目前没有待审核内容</strong><p>可以先手动建立资料，或在上方主动授权 AI 解析。上传文件本身不会调用模型或生成候选。</p></div></div> : <div className="candidate-list">{pending.map((candidate, index) => {
    const group = candidateGroup[candidate.category];
    const supported = candidate.evidence_status === "supported";
    return <article key={candidate.id} className="candidate-card" data-candidate-id={candidate.id} aria-label={`待审核事实 ${index + 1}`}><div className="candidate-card-heading"><strong>{groupNames[group]}</strong><span className={`badge ${supported ? "badge-neutral" : "badge-warning"}`}>{evidenceNames[candidate.evidence_status]}</span></div><p className="candidate-value">{entrySummary(group, candidate.value as ProfileEntryInput)}</p><p className="candidate-origin">{candidate.extraction_metadata?.test_result ? "Mock 测试候选" : "模型提出的候选"} · 来自 {candidate.resume_file_name}</p><details className="profile-source"><summary>查看候选来源与证据</summary><div className="profile-source-body"><Link href={`/resumes/${candidate.resume_id}`} className="inline-link">查看来源简历</Link>{candidate.evidence.map((evidence, number) => <div key={`${evidence.field}-${number}`} className="evidence-snippet"><strong>{fields[group].find(field => field.key === evidence.field)?.label || "原文片段"}</strong><blockquote>{evidence.source_text}</blockquote><span>{supported ? "简历原文（仅文字支持，真实性需由你核实）" : "候选引用（未获可靠原文支持）"}</span></div>)}{!candidate.evidence.length && <p>没有可靠的原文证据，请根据本人实际情况修改，或拒绝这项候选。</p>}</div></details><CandidateExtractionDetails candidate={candidate} />{!supported && <p className="candidate-warning">这项内容不能直接确认为事实。请核对原文，按实际情况修改，或拒绝。</p>}<div className="candidate-actions"><button className="button button-primary" disabled={disabled || !!reviewing || !supported} onClick={() => { void onReview(candidate, "confirm"); }}>{reviewing === candidate.id ? <Spinner label="正在审核" /> : <><Check size={15} />确认事实</>}</button><button className="button button-secondary" disabled={disabled || !!reviewing} onClick={() => { setEditing(candidate); setValue({ ...candidate.value }); setVerified(false); setIssue(null); }}><Pencil size={14} />修改后确认</button><button className="text-button" disabled={disabled || !!reviewing} onClick={() => { void onReview(candidate, "reject"); }}>拒绝候选</button></div></article>;
  })}</div>}{!!reviewed.length && <details className="review-history"><summary>已审核记录（{reviewed.length}）</summary>{reviewed.map(candidate => <div className="review-history-row" key={candidate.id}><div><strong>{groupNames[candidateGroup[candidate.category]]}</strong><p>原始候选：{entrySummary(candidateGroup[candidate.category], candidate.value as ProfileEntryInput)}</p><span>来源：{candidate.resume_file_name}{candidate.extraction_metadata?.test_result && " · Mock 测试结果"}</span><ReviewedEvidence candidate={candidate} /><CandidateExtractionDetails candidate={candidate} /></div><span className={`badge ${candidate.status === "rejected" ? "badge-neutral" : "badge-success"}`}>{statusNames[candidate.status]}</span></div>)}</details>}<Dialog.Root open={!!editing} onOpenChange={(open) => { if (!open && !reviewing) setEditing(null); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content profile-review-dialog" onEscapeKeyDown={event => { if (reviewing) event.preventDefault(); }} onInteractOutside={event => { if (reviewing) event.preventDefault(); }}><div className="dialog-heading"><div><Dialog.Title>修改候选事实</Dialog.Title><Dialog.Description>按本人实际情况填写。修改后的内容标记为用户修订，原文作为参考保留。</Dialog.Description></div><Dialog.Close className="icon-button dialog-close" aria-label="关闭事实修改窗口" disabled={!!reviewing}><X size={19} /></Dialog.Close></div>{editing && value && <form onSubmit={event => { event.preventDefault(); void modify(); }}><FieldEditor group={candidateGroup[editing.category]} entry={value} prefix="candidate-edit" disabled={!!reviewing} onChange={(key, next) => { setValue(current => current ? { ...current, [key]: next } : current); setVerified(false); }} /><label className="profile-attestation"><input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} disabled={!!reviewing} />我已核实以上内容符合本人实际情况</label>{(issue || error) && <Notice>{issue || error}</Notice>}<div className="dialog-actions"><Dialog.Close className="button button-secondary" disabled={!!reviewing}>取消</Dialog.Close><button className="button button-primary" type="submit" disabled={disabled || !verified || !!reviewing}>{reviewing ? <Spinner label="正在保存" /> : "保存修改并确认"}</button></div></form>}</Dialog.Content></Dialog.Portal></Dialog.Root></section>;
}
