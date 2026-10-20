"use client";

import { ApiError, formatError, type Application } from "@/lib/api";

export const applicationStatuses: Record<Application["status"], string> = {
  preparing: "准备投递", submitted: "已投递", assessment: "笔试", interview: "面试",
  offer: "收到 Offer", rejected: "被拒绝", withdrawn: "主动撤回", closed: "已结束",
};
export const actionStatuses = { pending: "待完成", completed: "已完成", cancelled: "已取消" };
export const eventLabels: Record<string, string> = {
  application_created: "创建申请", status_changed: "更新申请状态", notes_changed: "修改申请备注",
  next_action_changed: "更新下一步行动", submitted_at_changed: "调整实际投递时间",
};
export function ApplicationBadge({ status }: { status: Application["status"] }) {
  return <span className={`badge ${status === "offer" ? "badge-success" : "badge-neutral"}`}>{applicationStatuses[status]}</span>;
}
export function applicationError(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 404) return "找不到这条申请记录，可能已被删除或当前账户无法访问。";
  return formatError(cause);
}
export function localDateTime(value: string | null | undefined) {
  if (!value) return "";
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(item => item.type === type)?.value || "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
export function shanghaiInstant(value: string): string | null {
  if (!value) return null;
  const date = new Date(`${value}+08:00`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
export function actionDue(action: Application["next_action"]) {
  if (!action?.due_date) return "未设截止日期";
  return `${action.due_date}${action.due_time ? ` ${action.due_time.slice(0, 5)}（北京时间）` : " · 仅日期"}`;
}
export function isTerminal(status: Application["status"]) {
  return ["rejected", "withdrawn", "closed"].includes(status);
}
