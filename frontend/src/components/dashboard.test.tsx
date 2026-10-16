import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, clearSession, type Application, type DashboardData } from "@/lib/api";
import { Dashboard } from "./dashboard";

const auth = vi.hoisted(() => ({ id: "workbench-owner", status: "authenticated", replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => auth }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: auth.status === "authenticated" ? { id: auth.id, email: "synthetic@example.com" } : null, status: auth.status, signOut: vi.fn() }) }));
const timestamp = "2026-10-14T16:15:42.123Z";
const action = { title: "准备技术面试", notes: "只使用真实项目", due_date: "2026-10-15", due_time: null, timezone: "Asia/Shanghai" as const, status: "pending" as const };
const item = { application_id: "application-id", job_id: "job-id", job_title: "合成软件实习", company: "合成科技", revision: 2, application_status: "interview" as const, next_action: action, is_overdue: false };
function application(): Application { return { id: item.application_id, job_id: item.job_id, job_title: item.job_title, company: item.company, location: null, attempt_number: 1, status: item.application_status, next_action: action, notes: "保留完整备注", submitted_at: timestamp, created_at: timestamp, updated_at: timestamp, revision: 2, is_overdue: false, match: null }; }
function dashboard(overrides: Partial<DashboardData> = {}): DashboardData {
  return { generated_at: timestamp, today: "2026-10-15", timezone: "Asia/Shanghai", actions: { overdue: { total: 0, items: [] }, today: { total: 0, items: [] }, upcoming: { total: 0, items: [] }, undated: { total: 0, items: [] }, completed: { total: 0, items: [] }, cancelled: { total: 0, items: [] } }, summary: { total: 0, preparing: 0, submitted: 0, assessment_interview: 0, offer: 0, ended: 0 }, jobs: { total: 0, items: [] }, recent_activity: [], onboarding: { resume_count: 0, profile_entry_count: 0, job_count: 0, application_count: 0, next_step: "upload_resume", job_id: null }, ...overrides };
}
function withAction() { const data = dashboard(); data.actions.today = { total: 1, items: [item] }; data.summary = { ...data.summary, total: 1, assessment_interview: 1 }; data.onboarding.next_step = "manage_actions"; return data; }
beforeEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); clearSession(); auth.id = "workbench-owner"; auth.status = "authenticated"; });

it("puts first-use guidance before the empty action panel without inventing progress", async () => {
  vi.spyOn(api, "dashboard").mockResolvedValue(dashboard()); render(<Dashboard />);
  const guide = await screen.findByRole("region", { name: "建议的下一步" });
  expect(within(guide).getByRole("button", { name: "上传第一份简历" })).toBeVisible();
  expect(guide.compareDocumentPosition(screen.getByRole("region", { name: "今日与近期行动" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText(/共 0 条独立申请/)).toBeVisible();
  expect(screen.queryByRole("region", { name: "已逾期" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "导入职位" })[0]).toHaveAttribute("href", "/jobs/new");
});

it.each([
  ["complete_profile", "完善我的资料", "/profile"], ["import_job", "导入第一个岗位", "/jobs/new"],
  ["review_job", "审核 JD 条件", "/jobs/job-id"], ["match_job", "查看岗位匹配", "/jobs/job-id#job-match"],
  ["create_application", "创建投递计划", "/jobs/job-id"], ["manage_actions", "管理申请与下一步", "/applications"],
] as const)("uses existing progress for contextual %s guidance", async (step, label, href) => {
  const data = dashboard(); data.onboarding = { ...data.onboarding, next_step: step, job_id: "job-id", resume_count: 1 };
  vi.spyOn(api, "dashboard").mockResolvedValue(data); render(<Dashboard />);
  const guide = await screen.findByRole("region", { name: "建议的下一步" });
  expect(within(guide).getByRole("link", { name: label })).toHaveAttribute("href", href);
  expect(screen.queryByRole("button", { name: "上传第一份简历" })).not.toBeInTheDocument();
});

it("renders server date groups, exact Offer semantics, actionable reasons and activity without deriving counts", async () => {
  const data = withAction(); data.summary.offer = 3; data.summary.total = 4;
  data.actions.overdue = { total: 7, items: [{ ...item, application_id: "overdue", next_action: { ...action, title: "跟进 HR", due_date: "2026-10-14" }, is_overdue: true }] };
  data.actions.upcoming = { total: 1, items: [{ ...item, application_id: "upcoming", next_action: { ...action, title: "准备笔试", due_date: "2026-10-22" } }] };
  data.actions.undated = { total: 1, items: [{ ...item, application_id: "undated", next_action: { ...action, title: "整理作品", due_date: null } }] };
  data.jobs = { total: 1, items: [{ id: "job-id", title: "合成软件实习", company: "合成科技", reasons: ["favorite_without_application", "match_stale"], next_step: "match", updated_at: timestamp }] };
  data.recent_activity = [{ id: "event", application_id: item.application_id, job_title: item.job_title, company: item.company, type: "status_changed", action_status: null, application_status: "submitted", recorded_at: timestamp }];
  vi.spyOn(api, "dashboard").mockResolvedValue(data); render(<Dashboard />);
  const overdue = await screen.findByRole("region", { name: "已逾期" });
  expect(within(overdue).getByText("7")).toBeVisible(); expect(within(overdue).getByText("已逾期 · 待完成")).toBeVisible();
  expect(within(screen.getByRole("region", { name: "未来七天" })).getByText("2026-10-22 · 仅日期")).toBeVisible();
  expect(within(screen.getByRole("article", { name: "整理作品" })).getByText("未设截止日期")).toBeVisible();
  expect(screen.getByText(/收到 Offer 不表示已经接受/)).toBeVisible(); expect(screen.getByText("已收藏，尚未创建申请")).toBeVisible();
  expect(screen.getByRole("link", { name: "查看匹配" })).toHaveAttribute("href", "/jobs/job-id#job-match");
  expect(screen.getByText("更新申请状态 · 已投递")).toBeVisible();
  expect(within(screen.getByRole("article", { name: action.title })).getByRole("link", { name: "修改下一步" })).toHaveAttribute("href", "/applications/application-id#application-next-action");
});

it("completes using the shown revision, preserves unrelated fields and refreshes all server groups", async () => {
  const initial = withAction(); const saved = dashboard(); saved.actions.completed = { total: 1, items: [{ ...item, revision: 3, next_action: { ...action, status: "completed" } }] };
  vi.spyOn(api, "dashboard").mockResolvedValueOnce(initial).mockResolvedValue(saved);
  vi.spyOn(api, "application").mockResolvedValue(application()); const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application());
  render(<Dashboard />); const user = userEvent.setup(); await user.click(await screen.findByRole("button", { name: "完成行动" }));
  await screen.findByText("行动已完成，工作台已同步。");
  expect(update.mock.calls[0][1]).toMatchObject({ revision: 2, notes: "保留完整备注", submitted_at: timestamp, status: "interview", next_action: { ...action, status: "completed" }, idempotency_key: expect.any(String) });
  expect(screen.queryByRole("region", { name: "今天需要完成" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("已完成与已取消的行动（1）")); expect(screen.getByRole("button", { name: "恢复待完成" })).toBeVisible();
});

it("requires cancellation confirmation and supports restoring through the same application API", async () => {
  const data = withAction(); const cancelled = dashboard(); cancelled.actions.cancelled = { total: 1, items: [{ ...item, revision: 3, next_action: { ...action, status: "cancelled" } }] };
  vi.spyOn(api, "dashboard").mockResolvedValueOnce(data).mockResolvedValueOnce(cancelled).mockResolvedValue(data);
  const read = vi.spyOn(api, "application").mockResolvedValueOnce(application()).mockResolvedValueOnce({ ...application(), revision: 3, next_action: { ...action, status: "cancelled" } });
  const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application());
  render(<Dashboard />); const user = userEvent.setup(); await user.click(await screen.findByRole("button", { name: "取消行动" }));
  expect(read).not.toHaveBeenCalled(); await user.click(screen.getByRole("button", { name: "确认取消行动" }));
  await screen.findByText("行动已取消，工作台已同步。"); fireEvent.click(screen.getByText("已完成与已取消的行动（1）"));
  await user.click(screen.getByRole("button", { name: "恢复待完成" })); await screen.findByText("行动已恢复为待完成，工作台已同步。");
  expect(update.mock.calls.map(call => call[1].next_action?.status)).toEqual(["cancelled", "pending"]);
});

it("retains an unknown-response request key and never automatically repeats a write", async () => {
  vi.spyOn(api, "dashboard").mockResolvedValue(withAction()); const read = vi.spyOn(api, "application").mockResolvedValue(application());
  const update = vi.spyOn(api, "updateApplication").mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue(application());
  render(<Dashboard />); const user = userEvent.setup(); const button = await screen.findByRole("button", { name: "完成行动" });
  await user.click(button); await screen.findByRole("alert"); expect(update).toHaveBeenCalledOnce();
  await user.click(button); await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  expect(update.mock.calls[0][1]).toEqual(update.mock.calls[1][1]); expect(read).toHaveBeenCalledOnce();
});

it("blocks stale row writes until the workbench has been refreshed", async () => {
  vi.spyOn(api, "dashboard").mockResolvedValue(withAction()); vi.spyOn(api, "application").mockResolvedValue({ ...application(), revision: 3, next_action: { ...action, title: "不同的行动" } });
  const update = vi.spyOn(api, "updateApplication"); render(<Dashboard />); const user = userEvent.setup(); await user.click(await screen.findByRole("button", { name: "完成行动" }));
  await screen.findByRole("alert"); expect(update).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "完成行动" })).toBeDisabled();
  await user.click(within(screen.getByRole("alert")).getByRole("button", { name: "刷新工作台" })); await waitFor(() => expect(screen.getByRole("button", { name: "完成行动" })).toBeEnabled());
});

it("does not offer restore for ended applications and does not count cancelled actions as overdue", async () => {
  const data = dashboard(); data.actions.cancelled = { total: 1, items: [{ ...item, application_status: "closed", next_action: { ...action, status: "cancelled", due_date: "2025-01-01" } }] };
  vi.spyOn(api, "dashboard").mockResolvedValue(data); render(<Dashboard />); await screen.findByText("已完成与已取消的行动（1）");
  fireEvent.click(screen.getByText("已完成与已取消的行动（1）")); expect(screen.getByRole("button", { name: "恢复待完成" })).toBeDisabled();
  expect(screen.queryByRole("region", { name: "已逾期" })).not.toBeInTheDocument(); expect(screen.getByText(/申请已结束，重新开启后可恢复行动/)).toBeVisible();
});

it("provides a recovery action for failed reads", async () => {
  vi.spyOn(api, "dashboard").mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue(dashboard()); render(<Dashboard />); const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "重新加载工作台" })); await screen.findByRole("button", { name: "上传第一份简历" });
});

it("hides prior-account data immediately and ignores late responses after a session reset", async () => {
  let finish!: (value: DashboardData) => void; vi.spyOn(api, "dashboard").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<Dashboard />); await waitFor(() => expect(api.dashboard).toHaveBeenCalledOnce());
  act(() => clearSession()); await act(async () => finish(withAction()));
  expect(screen.queryByRole("article", { name: action.title })).not.toBeInTheDocument();
  auth.status = "anonymous"; view.rerender(<Dashboard />); expect(auth.replace).toHaveBeenCalledWith("/login?reason=expired");
});
