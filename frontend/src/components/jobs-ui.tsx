"use client";

import { ApiError, formatError, type JobRequirementInput } from "@/lib/api";

export const workTypes = { unknown: "未填写", internship: "实习", campus: "校招", full_time: "正式工作" };
export const priorities = { must: "必须", preferred: "优先", uncertain: "待核实" };
export const requirementTypes: Record<JobRequirementInput["type"], string> = {
  education: "学历要求", major: "专业要求", graduation: "毕业年份 / 届别", experience: "工作经验",
  location: "地点 / 到岗要求", internship_duration: "实习时长", technical_skill: "技术技能",
  project: "相关项目经历", internship_experience: "相关实习经历", language: "语言 / 其他能力",
  other: "其他条件", salary: "薪资及单位", work_type: "工作性质", responsibility: "岗位职责",
};

export function jobError(error: unknown): string {
  return error instanceof ApiError && error.status === 404 ? "找不到这个职位，可能已删除或当前账户无法访问。" : formatError(error);
}

// The backend also validates links. This guard keeps unexpected or legacy
// response data from becoming a dangerous clickable link in the browser.
export function safeJobLink(value: string | null | undefined): string | null {
  if (!value || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function reviewLabel(summary: { total: number; pending: number; confirmed: number; stale: number }): string {
  if (summary.stale) return `${summary.stale} 项需重新审核`;
  if (!summary.total) return "尚无结构化条件";
  return summary.pending ? `${summary.pending} 项待审核` : "条件已审核";
}
