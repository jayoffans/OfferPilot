"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { CareerProfile, EducationInput, ExperienceInput, ProfileSave, ProjectInput, SkillInput } from "@/lib/api";

export type ProfileGroup = "education" | "skills" | "projects" | "experiences";
export type ProfileDraft = Pick<ProfileSave, ProfileGroup>;
export type ProfileEntryInput = EducationInput | SkillInput | ProjectInput | ExperienceInput;
type Source = CareerProfile["education"][number]["source"];
type Field = { key: string; label: string; max?: number; type?: "month" | "textarea" | "select"; options?: { value: string; label: string }[] };

export const groupNames: Record<ProfileGroup, string> = { education: "教育经历", skills: "技术技能", projects: "项目经历", experiences: "实习 / 工作经历" };
export const fields: Record<ProfileGroup, Field[]> = {
  education: [
    { key: "school", label: "学校", max: 255 },
    { key: "degree", label: "学历", max: 255 },
    { key: "major", label: "专业", max: 255 },
    { key: "start_date", label: "入学时间", type: "month" },
    { key: "end_date", label: "毕业时间", type: "month" },
    { key: "end_date_kind", label: "毕业时间类型", type: "select", options: [{ value: "expected", label: "预计毕业" }, { value: "actual", label: "实际毕业" }] },
  ],
  skills: [
    { key: "name", label: "技能名称", max: 255 },
    { key: "level", label: "掌握情况", max: 255 },
  ],
  projects: [
    { key: "name", label: "项目名称", max: 255 },
    { key: "role", label: "项目角色", max: 255 },
    { key: "start_date", label: "开始时间", type: "month" },
    { key: "end_date", label: "结束时间", type: "month" },
    { key: "is_current", label: "项目状态", type: "select", options: [{ value: "true", label: "进行中" }, { value: "false", label: "已结束" }] },
    { key: "description", label: "项目内容与成果", type: "textarea", max: 3000 },
  ],
  experiences: [
    { key: "organization", label: "单位名称", max: 255 },
    { key: "role", label: "岗位 / 职责", max: 255 },
    { key: "experience_type", label: "经历类型", type: "select", options: [{ value: "internship", label: "实习" }, { value: "work", label: "工作" }] },
    { key: "start_date", label: "开始时间", type: "month" },
    { key: "end_date", label: "结束时间", type: "month" },
    { key: "is_current", label: "在职状态", type: "select", options: [{ value: "true", label: "仍在职" }, { value: "false", label: "已结束" }] },
    { key: "description", label: "经历内容与成果", type: "textarea", max: 3000 },
  ],
};

export function emptyEntry(group: ProfileGroup): ProfileEntryInput {
  if (group === "education") return { school: null, degree: null, major: null, start_date: null, end_date: null, end_date_kind: null };
  if (group === "skills") return { name: null, level: null };
  if (group === "projects") return { name: null, role: null, description: null, start_date: null, end_date: null, is_current: null };
  return { organization: null, role: null, experience_type: null, description: null, start_date: null, end_date: null, is_current: null };
}

export function profileDraft(profile: CareerProfile): ProfileDraft {
  return {
    education: profile.education.map(({ source, ...entry }) => { void source; return entry; }),
    skills: profile.skills.map(({ source, ...entry }) => { void source; return entry; }),
    projects: profile.projects.map(({ source, ...entry }) => { void source; return entry; }),
    experiences: profile.experiences.map(({ source, ...entry }) => { void source; return entry; }),
  };
}

export function fieldValue(entry: ProfileEntryInput, key: string): unknown {
  return (entry as unknown as Record<string, unknown>)[key];
}

export function entrySummary(group: ProfileGroup, entry: ProfileEntryInput): string {
  return fields[group].map(field => {
    const value = fieldValue(entry, field.key);
    if (value === null || value === undefined || value === "") return null;
    const display = field.options?.find(option => option.value === String(value))?.label || String(value);
    return `${field.label}：${display}`;
  }).filter(Boolean).join(" · ") || "内容尚未填写";
}

export function FieldEditor({ group, entry, prefix, disabled, source, original, onChange }: { group: ProfileGroup; entry: ProfileEntryInput; prefix: string; disabled?: boolean; source?: Source; original?: ProfileEntryInput; onChange(key: string, value: string | boolean | null): void }) {
  return <div className="profile-fields">{fields[group].map(field => {
    const id = `${prefix}-${field.key}`;
    const raw = fieldValue(entry, field.key);
    const value = raw === null || raw === undefined ? "" : String(raw);
    const changed = !!original && raw !== fieldValue(original, field.key);
    const origin = changed || !source ? "用户填写" : source.field_sources[field.key] === "resume" ? source.test_result ? "Mock 测试结果经用户确认" : "简历原文" : source.field_sources[field.key] === "resume_deleted" ? "来源简历已删除" : "用户填写";
    const common = { id, value, disabled, "aria-describedby": `${id}-source`, onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => { const next = event.target.value; onChange(field.key, next === "" ? null : field.key === "is_current" ? next === "true" : next); } };
    return <div className={`form-field${field.type === "textarea" ? " profile-field-wide" : ""}`} key={field.key}><label htmlFor={id}>{field.label}</label>{field.type === "select" ? <select {...common}><option value="">未填写 / 未知</option>{field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : field.type === "textarea" ? <textarea {...common} rows={4} maxLength={field.max} /> : <input {...common} type={field.type || "text"} maxLength={field.max} autoComplete="off" min={field.type === "month" ? "1900-01" : undefined} max={field.type === "month" ? "2199-12" : undefined} />}<p className="field-hint" id={`${id}-source`}>{value ? `来源：${origin}${changed ? " · 待保存" : ""}` : "未填写，保持未知"}</p></div>;
  })}</div>;
}

export function SourceDetails({ group, source, entry, original }: { group: ProfileGroup; source?: Source; entry: ProfileEntryInput; original?: ProfileEntryInput }) {
  const labels: Record<Source["kind"], string> = { manual: "用户填写", resume_confirmed: "简历候选，经用户确认", user_modified: "用户填写或修订", resume_deleted: "来源简历已删除，已确认内容仍保留" };
  return <details className="profile-source"><summary>查看来源与证据</summary><div className="profile-source-body"><p>{source ? labels[source.kind] : "用户填写 · 保存后成为正式资料"}</p>{source?.test_result && <p className="candidate-warning">来源为 Mock 测试结果，经用户主动确认或修订；不代表真实 AI 抽取。</p>}{source?.provider === "deepseek" && <p>AI 来源：DeepSeek / {source.model} · 运行 {source.run_id} · 经用户审核</p>}{source?.resume_file_name && <p>来源简历：{source.resume_file_name}{source.resume_id && <Link href={`/resumes/${source.resume_id}`} className="text-button">查看文件<ExternalLink size={12} /></Link>}</p>}{source?.evidence.map((evidence, index) => {
    const unchanged = original && fieldValue(entry, evidence.field) === fieldValue(original, evidence.field);
    return <div className="evidence-snippet" key={`${evidence.field}-${index}`}><strong>{fields[group].find(field => field.key === evidence.field)?.label || "原文证据"}</strong><blockquote>{evidence.source_text}</blockquote><span>{evidence.supports_current_value && unchanged ? "原文文字支持当前值，已由你确认" : "历史原文仅供参考，不代表当前值；当前值由你填写或修订"}</span></div>;
  })}{!source?.evidence.length && <p className="profile-muted">{source?.kind === "resume_deleted" ? "简历及对应原文证据已删除。" : "这项资料由你提供，没有简历原文证据。"}</p>}</div></details>;
}

export function validateDraft(draft: ProfileDraft): string | null {
  for (const group of Object.keys(groupNames) as ProfileGroup[]) {
    for (const [index, entry] of draft[group].entries()) {
      const title = `${groupNames[group]}第 ${index + 1} 项`;
      if (!fields[group].some(field => {
        const value = fieldValue(entry, field.key);
        return typeof value === "string" ? value.trim().length > 0 : value !== null && value !== undefined;
      })) return `${title}尚未填写任何内容。请补充一项信息，或移除这条空记录。`;
      const start = fieldValue(entry, "start_date");
      const end = fieldValue(entry, "end_date");
      if (typeof start === "string" && typeof end === "string" && start > end) return `${title}的结束时间不能早于开始时间。`;
      if (fieldValue(entry, "is_current") === true && end) return `${title}仍在进行，请清空结束时间，或修改当前状态。`;
      if (fieldValue(entry, "end_date_kind") && !end) return `${title}已选择毕业时间类型，请填写毕业时间，或将类型恢复为未知。`;
    }
  }
  return null;
}
