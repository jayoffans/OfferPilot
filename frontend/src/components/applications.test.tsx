import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, clearSession, type Application, type ApplicationEvents, type Job } from "@/lib/api";
import { ApplicationPage } from "./application-page";
import { ApplicationsPage } from "./applications-page";
import { JobApplicationsPanel } from "./job-applications";
import { actionDue, localDateTime, shanghaiInstant } from "./applications-ui";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), pathname: "/applications", userId: "application-owner", query: "" }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname, useSearchParams: () => new URLSearchParams(mocks.query), useRouter: () => mocks }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "authenticated", user: { id: mocks.userId, email: "synthetic-app@example.com" }, signOut: vi.fn() }) }));
const id = "11111111-1111-4111-8111-111111111111";
const jobId = "22222222-2222-4222-8222-222222222222";
const date = "2026-10-14T16:15:00Z";
const action = { title: "准备技术面试", notes: "复习真实项目", due_date: "2026-10-16", due_time: null, timezone: "Asia/Shanghai" as const, status: "pending" as const };
function application(overrides: Partial<Application> = {}): Application { return { id, job_id: jobId, job_title: "合成软件实习生", company: "合成科技", location: "上海", status: "preparing", attempt_number: 1, submitted_at: null, notes: "", next_action: null, revision: 1, created_at: date, updated_at: date, is_overdue: false, match: null, ...overrides }; }
function events(overrides: Partial<ApplicationEvents> = {}): ApplicationEvents { return { items: [{ id: "event-id", type: "application_created", occurred_at: date, recorded_at: date, revision: 1, changes: { before: null, after: { status: "preparing" } } }], total: 1, page: 1, page_size: 20, ...overrides }; }
function savedJob(): Job { return { id: jobId, title: "合成软件实习生", company: "合成科技", location: "上海", revision: 3 } as Job; }
beforeEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); clearSession(); mocks.userId = "application-owner"; mocks.pathname = "/applications"; mocks.query = ""; vi.spyOn(api, "applicationEvents").mockResolvedValue(events()); vi.spyOn(api, "latestMatch").mockResolvedValue(null); });
async function detail(data = application()) { vi.spyOn(api, "application").mockResolvedValue(data); render(<ApplicationPage id={id} />); await screen.findByLabelText("申请状态", { exact: true }); return userEvent.setup(); }

it("shows a truthful empty state and sends server search, status, sort and paging filters", async () => {
  const list = vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 12 });
  render(<ApplicationsPage />);
  await screen.findByRole("heading", { name: "从一个认真考虑的机会开始" });
  expect(screen.getByText(/收藏职位不会自动创建申请/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("搜索申请"), { target: { value: "合成科技" } });
  fireEvent.change(screen.getByLabelText("申请状态筛选"), { target: { value: "interview" } });
  fireEvent.change(screen.getByLabelText("申请排序"), { target: { value: "next_action_asc" } });
  await waitFor(() => expect(list.mock.calls.at(-1)?.[0].get("q")).toBe("合成科技"));
  expect(list.mock.calls.at(-1)?.[0].get("status")).toBe("interview"); expect(list.mock.calls.at(-1)?.[0].get("sort")).toBe("next_action_asc");
  await screen.findByRole("heading", { name: "没有找到符合筛选的申请" });
});

it("renders overdue actions and unknown actual submission honestly", async () => {
  vi.spyOn(api, "applications").mockResolvedValue({ items: [application({ next_action: action, is_overdue: true })], total: 1, page: 1, page_size: 12 });
  render(<ApplicationsPage />); await screen.findByText("合成软件实习生");
  expect(screen.getByText("实际投递时间未知")).toBeVisible(); expect(screen.getByText("已逾期 · 待完成")).toBeVisible(); expect(screen.getByText("2026-10-16 · 仅日期")).toBeVisible();
});

it("creates preparing directly from a saved job with no duplicate company fields", async () => {
  vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 });
  const create = vi.spyOn(api, "createApplication").mockResolvedValue(application());
  render(<JobApplicationsPanel job={savedJob()} />); const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "准备投递" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(`/applications/${id}`));
  expect(create.mock.calls[0][0]).toEqual({ job_id: jobId, job_revision: 3, reapply: false, match_id: null, idempotency_key: expect.any(String) });
  expect(screen.queryByLabelText("公司名称")).not.toBeInTheDocument();
});

it("an existing active record has a real continuation link and prevents accidental recreation", async () => {
  vi.spyOn(api, "applications").mockResolvedValue({ items: [application()], total: 1, page: 1, page_size: 100 }); const create = vi.spyOn(api, "createApplication");
  render(<JobApplicationsPanel job={savedJob()} />);
  expect(await screen.findByRole("link", { name: "继续跟踪申请" })).toHaveAttribute("href", `/applications/${id}`);
  expect(screen.queryByRole("button", { name: "准备投递" })).not.toBeInTheDocument(); expect(create).not.toHaveBeenCalled();
});

it("a new attempt needs explicit confirmation and preserves previous history", async () => {
  vi.spyOn(api, "applications").mockResolvedValue({ items: [application({ status: "rejected" })], total: 1, page: 1, page_size: 100 }); const create = vi.spyOn(api, "createApplication").mockResolvedValue(application({ attempt_number: 2 }));
  render(<JobApplicationsPanel job={savedJob()} />); const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "创建新一轮申请" })); expect(create).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认创建新一轮" })); await waitFor(() => expect(create).toHaveBeenCalledOnce()); expect(create.mock.calls[0][0].reapply).toBe(true);
});

it("retains the create request key when a network response is unknown", async () => {
  vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 }); const create = vi.spyOn(api, "createApplication").mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(application());
  render(<JobApplicationsPanel job={savedJob()} />); const user = userEvent.setup(); const button = await screen.findByRole("button", { name: "准备投递" });
  await user.click(button); await screen.findByRole("alert"); await user.click(button); await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][0].idempotency_key).toBe(create.mock.calls[1][0].idempotency_key);
});

it("allows skipping and correcting states while leaving unknown submission time null", async () => {
  const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application({ status: "interview", revision: 2 })); const user = await detail();
  await user.selectOptions(screen.getByLabelText("申请状态", { exact: true }), "interview"); await user.click(screen.getByRole("button", { name: "保存申请修改" }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce()); expect(update.mock.calls[0][1]).toMatchObject({ revision: 1, status: "interview", submitted_at: null });
  expect(screen.getByLabelText("申请状态", { exact: true })).toHaveValue("interview");
  await user.selectOptions(screen.getByLabelText("申请状态", { exact: true }), "assessment"); expect(screen.getByRole("button", { name: "保存申请修改" })).toBeEnabled();
});

it("persists notes and date-only actions without generating a time; Shanghai converts across UTC day", async () => {
  const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application({ notes: "真实备注", next_action: action, submitted_at: "2026-10-13T16:15:00Z", revision: 2 })); const user = await detail();
  fireEvent.change(screen.getByLabelText("实际投递时间（北京时间）"), { target: { value: "2026-10-14T00:15" } }); fireEvent.change(screen.getByLabelText("申请备注", { exact: true }), { target: { value: "真实备注" } });
  fireEvent.change(screen.getByLabelText("下一步行动", { exact: true }), { target: { value: "准备技术面试" } }); fireEvent.change(screen.getByLabelText("行动截止日期"), { target: { value: "2026-10-16" } });
  await user.click(screen.getByRole("button", { name: "保存申请修改" })); await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update.mock.calls[0][1]).toMatchObject({ notes: "真实备注", submitted_at: "2026-10-13T16:15:00.000Z", next_action: { due_date: "2026-10-16", due_time: null, timezone: "Asia/Shanghai", status: "pending" } });
});

it("completes and cancels actions through real writes with revisions and confirmation", async () => {
  let current = application({ next_action: action });
  const update = vi.spyOn(api, "updateApplication").mockImplementation(async (_id, data) => { current = { ...current, ...data, revision: current.revision + 1 }; return current; });
  const user = await detail(current);
  await user.click(screen.getByRole("button", { name: "完成行动" })); await screen.findByRole("button", { name: "恢复待完成" }); expect(update.mock.calls[0][1].next_action?.status).toBe("completed");
  await user.click(screen.getByRole("button", { name: "恢复待完成" })); await screen.findByRole("button", { name: "取消行动" });
  await user.click(screen.getByRole("button", { name: "取消行动" })); expect(update).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole("button", { name: "确认取消行动" })); await waitFor(() => expect(update).toHaveBeenCalledTimes(3)); expect(update.mock.calls[2][1]).toMatchObject({ revision: 3, next_action: { status: "cancelled" } });
});

it("retains unchanged mutation intent after an unknown save response and does not automatically retry", async () => {
  const update = vi.spyOn(api, "updateApplication").mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue(application({ notes: "保存后响应丢失", revision: 2 })); const user = await detail();
  await user.type(screen.getByLabelText("申请备注", { exact: true }), "保存后响应丢失"); await user.click(screen.getByRole("button", { name: "保存申请修改" })); await screen.findByRole("alert");
  expect(update).toHaveBeenCalledOnce(); expect(screen.getByLabelText("申请备注", { exact: true })).toHaveValue("保存后响应丢失");
  await user.click(screen.getByRole("button", { name: "保存申请修改" })); await waitFor(() => expect(update).toHaveBeenCalledTimes(2)); expect(update.mock.calls[1][1]).toEqual(update.mock.calls[0][1]);
});

it("409 keeps local edits, blocks overwrite, and requires explicit discard before reloading", async () => {
  vi.spyOn(api, "updateApplication").mockRejectedValue(new ApiError(409, "application_conflict", "记录已更新")); const user = await detail();
  await user.type(screen.getByLabelText("申请备注", { exact: true }), "保留本地输入"); await user.click(screen.getByRole("button", { name: "保存申请修改" })); await screen.findByRole("alert");
  expect(screen.getByLabelText("申请备注", { exact: true })).toHaveValue("保留本地输入"); expect(screen.getByRole("button", { name: "保存申请修改" })).toBeDisabled();
  vi.mocked(api.application).mockResolvedValue(application({ notes: "服务器新备注", revision: 2 })); await user.click(screen.getByRole("button", { name: "查看最新申请记录" })); await screen.findByText("备注：服务器新备注");
  expect(screen.getByLabelText("申请备注", { exact: true })).toHaveValue("保留本地输入"); await user.click(screen.getByRole("button", { name: "放弃本次输入，重新载入" }));
  await user.click(screen.getByRole("button", { name: "放弃输入并重新载入" })); await waitFor(() => expect(screen.getByLabelText("申请备注", { exact: true })).toHaveValue("服务器新备注"));
});

it("shows separate event and record times and renders malicious notes as plain text", async () => {
  const malicious = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  vi.mocked(api.applicationEvents).mockResolvedValue(events({ items: [{ id: "event-note", type: "notes_changed", occurred_at: "2026-10-13T16:01:00Z", recorded_at: date, revision: 2, changes: { before: "旧备注", after: malicious } }] }));
  await detail(application({ notes: malicious })); const timeline = screen.getByRole("complementary", { name: "申请时间线" });
  await within(timeline).findByText("修改申请备注"); fireEvent.click(within(timeline).getByText("查看变化内容")); expect(within(timeline).getByText(malicious)).toBeVisible();
  expect(timeline.querySelector("img")).toBeNull(); expect(timeline.querySelector("script")).toBeNull(); expect(within(timeline).getByText(/实际发生：2026/)).toBeVisible(); expect(within(timeline).getByText(/系统记录：2026/)).toBeVisible();
});

it("marks stale match references as historical without changing the application state", async () => {
  await detail(application({ match: { id: "old-match", stale: true, stale_reasons: ["profile_changed"], created_at: date, profile_revision: 1, job_revision: 2, jd_version: 1, rule_version: "match-rules-1.0" } }));
  expect(screen.getByText("关联的匹配分析已失效，仅作历史参考")).toBeVisible(); expect(screen.getByLabelText("申请状态", { exact: true })).toHaveValue("preparing"); expect(screen.getByRole("link", { name: "查看当前岗位匹配" })).toHaveAttribute("href", `/jobs/${jobId}#job-match`);
});

it("requires confirmation for ending and deleting, using explicit delete authorization", async () => {
  const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application({ status: "closed", revision: 2 })); const remove = vi.spyOn(api, "deleteApplication").mockResolvedValue(); const user = await detail();
  await user.selectOptions(screen.getByLabelText("申请状态", { exact: true }), "closed"); await user.click(screen.getByRole("button", { name: "保存申请修改" })); expect(update).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认保存状态" })); await waitFor(() => expect(update).toHaveBeenCalledOnce());
  await user.click(screen.getByRole("button", { name: "删除申请记录" })); expect(remove).not.toHaveBeenCalled(); await user.click(screen.getByRole("button", { name: "确认删除申请" })); await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/applications")); expect(remove.mock.calls[0][1]).toEqual({ revision: 2, idempotency_key: expect.any(String), confirmed: true });
});

it("uses explicit Shanghai conversions for midnight and preserves a date-only boundary", () => {
  expect(localDateTime("2026-10-14T16:15:00Z")).toBe("2026-10-15T00:15"); expect(shanghaiInstant("2026-10-15T00:15")).toBe("2026-10-14T16:15:00.000Z"); expect(shanghaiInstant("")).toBeNull(); expect(actionDue(action)).toBe("2026-10-16 · 仅日期");
});

it("editing notes preserves the original precise submitted instant", async () => {
  const precise = "2026-10-14T16:15:42.123Z";
  const update = vi.spyOn(api, "updateApplication").mockResolvedValue(application({ submitted_at: precise, notes: "仅修改备注", revision: 2 }));
  const user = await detail(application({ submitted_at: precise }));
  await user.type(screen.getByLabelText("申请备注", { exact: true }), "仅修改备注");
  await user.click(screen.getByRole("button", { name: "保存申请修改" }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update.mock.calls[0][1].submitted_at).toBe(precise);
});

it("loads next-action and overdue filters from the URL and preserves them in the detail return link", async () => {
  mocks.query = "q=合成&status=interview&next_action=pending&overdue=true&sort=next_action_asc&page=2";
  const list = vi.spyOn(api, "applications").mockResolvedValue({ items: [application({ next_action: action, status: "interview", is_overdue: true })], total: 15, page: 2, page_size: 12 });
  render(<ApplicationsPage />); await screen.findByText("合成软件实习生");
  expect(screen.getByLabelText("下一步行动筛选")).toHaveValue("pending"); expect(screen.getByLabelText("逾期筛选")).toHaveValue("true");
  const query = list.mock.calls.at(-1)![0]; expect(query.get("page")).toBe("2"); expect(query.get("overdue")).toBe("true");
  const href = screen.getByRole("link", { name: /合成软件实习生/ }).getAttribute("href")!;
  expect(new URL(href, "http://localhost").searchParams.get("returnTo")).toContain("next_action=pending&overdue=true&sort=next_action_asc&page=2");
});

it("normalizes malformed URL enum values and pages instead of forwarding inherited object keys", async () => {
  mocks.query = "status=__proto__&next_action=unknown&overdue=maybe&sort=toString&page=-1";
  const list = vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 12 });
  render(<ApplicationsPage />); await screen.findByRole("heading", { name: "从一个认真考虑的机会开始" });
  const query = list.mock.calls.at(-1)![0]; expect(query.get("status")).toBeNull(); expect(query.get("next_action")).toBeNull(); expect(query.get("overdue")).toBeNull(); expect(query.get("sort")).toBe("updated_desc"); expect(query.get("page")).toBe("1");
});

it("updates URL filters and resets pagination without dropping multiword company searches", async () => {
  const list = vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 12 });
  render(<ApplicationsPage />); await screen.findByRole("heading", { name: "从一个认真考虑的机会开始" });
  fireEvent.change(screen.getByLabelText("搜索申请"), { target: { value: "合成 科技" } });
  fireEvent.change(screen.getByLabelText("下一步行动筛选"), { target: { value: "none" } });
  fireEvent.change(screen.getByLabelText("逾期筛选"), { target: { value: "false" } });
  await waitFor(() => expect(list.mock.calls.at(-1)![0].get("q")).toBe("合成 科技"));
  const query = new URLSearchParams(window.location.search); expect(query.get("q")).toBe("合成 科技"); expect(query.get("next_action")).toBe("none"); expect(query.get("overdue")).toBe("false"); expect(query.get("page")).toBe("1");
});

it("provides the originating filtered list as the explicit back link", async () => {
  vi.spyOn(api, "application").mockResolvedValue(application()); render(<ApplicationPage id={id} backHref="/applications?status=offer&page=3" />);
  expect(screen.getByRole("link", { name: "返回投递进度" })).toHaveAttribute("href", "/applications?status=offer&page=3");
  await screen.findByLabelText("申请状态", { exact: true });
});

it("explains ended applications and prevents restoring cancelled pending work", async () => {
  await detail(application({ status: "closed", next_action: { ...action, status: "cancelled" } }));
  expect(screen.getByRole("button", { name: "恢复待完成" })).toBeDisabled(); expect(screen.getByText(/申请已结束，未完成行动会取消/)).toBeVisible();
});
