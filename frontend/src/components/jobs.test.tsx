import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, clearSession, type Job, type JobRequirement } from "@/lib/api";
import { readJobDraft } from "@/lib/job-drafts";
import { evidenceStart } from "./job-editor";
import { JobPage } from "./job-page";
import { JobsPage } from "./jobs-page";
import { safeJobLink } from "./jobs-ui";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), signOut: vi.fn(), pathname: "/jobs/new", userId: "synthetic-user" }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname, useRouter: () => mocks }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "authenticated", user: { id: mocks.userId, email: "synthetic@example.com" }, signOut: mocks.signOut }) }));
const id = "11111111-1111-4111-8111-111111111111";
const jd = "本科及以上学历。\n必须掌握 Python。\n熟悉 Java 优先。\n专业范围待核实。";
const date = "2026-10-14T00:00:00Z";
function requirement(overrides: Partial<JobRequirement> = {}): JobRequirement {
  return { id: "22222222-2222-4222-8222-222222222222", type: "technical_skill", content: "熟悉 Java 优先。", priority: "preferred", review_status: "pending", evidence_text: "熟悉 Java 优先。", source_start: Array.from(jd.slice(0, jd.indexOf("熟悉"))).length, source_end: Array.from(jd.slice(0, jd.indexOf("熟悉"))).length + 11, line_start: 3, line_end: 3, source_jd_version: 1, source_kind: "rule", stale: false, revision: 1, schema_version: "1.0", parser_version: "jd-rules-1.0", created_at: date, updated_at: date, reviewed_at: null, ...overrides };
}
function job(overrides: Partial<Job> = {}): Job {
  return { id, title: "合成后端实习生", company: "合成科技", location: "上海", salary: null, work_type: "internship", source_url: "https://careers.example.com/job", source_kind: "pasted", revision: 1, jd_version: 1, is_favorite: false, created_at: date, updated_at: date, review_summary: { total: 1, pending: 1, confirmed: 0, stale: 0 }, schema_version: "1.0", jd_text: jd, requirements: [requirement()], parser_version: "jd-rules-1.0", jd_snapshots: [{ version: 1, jd_text: jd, created_at: date }], ...overrides };
}
beforeEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); clearSession(); mocks.pathname = "/jobs/new"; mocks.userId = "synthetic-user"; });
async function newBasics() {
  render(<JobPage />); const user = userEvent.setup();
  await screen.findByLabelText("招聘 JD"); await user.click(screen.getByRole("button", { name: "手工录入" }));
  await user.type(screen.getByLabelText("岗位名称", { exact: true }), "手工合成职位");
  return user;
}
async function edit(data: Job = job()) {
  mocks.pathname = `/jobs/${id}`; vi.spyOn(api, "job").mockResolvedValue(data);
  render(<JobPage id={id} />); const user = userEvent.setup();
  await screen.findByRole("heading", { name: data.title }); await user.click(screen.getByRole("button", { name: "编辑职位" }));
  return user;
}

it("supports minimal manual import without a JD or inferred requirements", async () => {
  const parse = vi.spyOn(api, "parseJob");
  const create = vi.spyOn(api, "createJob").mockResolvedValue(job({ title: "手工合成职位", jd_text: "", requirements: [], source_kind: "manual" }));
  const user = await newBasics(); await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  expect(screen.getByRole("button", { name: "免费规则整理" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "保存职位" }));
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith(`/jobs/${id}`));
  expect(create.mock.calls[0][0]).toEqual({ title: "手工合成职位", company: null, location: null, salary: null, work_type: "unknown", source_url: null, jd_text: "", requirements: [], idempotency_key: expect.any(String) });
  expect(parse).not.toHaveBeenCalled();
});

it("only an explicit free-rule action creates pending proposals and keeps preferred wording", async () => {
  const parse = vi.spyOn(api, "parseJob").mockResolvedValue({ schema_version: "1.0", parser_version: "jd-rules-1.0", requirements: [requirement()], warnings: [] });
  const create = vi.spyOn(api, "createJob").mockResolvedValue(job());
  const user = userEvent.setup(); render(<JobPage />);
  await screen.findByLabelText("招聘 JD"); fireEvent.change(screen.getByLabelText("招聘 JD"), { target: { value: jd } });
  await user.click(screen.getByRole("button", { name: "继续填写基本信息" })); await user.type(screen.getByLabelText("岗位名称", { exact: true }), "合成后端实习生"); await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  expect(parse).not.toHaveBeenCalled(); await user.click(screen.getByRole("button", { name: "免费规则整理" }));
  const card = await screen.findByRole("article", { name: "条件第 1 项" });
  expect(within(card).getByLabelText("条件分类")).toHaveValue("preferred");
  expect(within(card).getByText("待审核")).toBeVisible();
  await user.click(within(card).getByRole("button", { name: "确认条件" }));
  fireEvent.change(within(card).getByLabelText("条件内容"), { target: { value: "Java 为加分项（人工修订）" } });
  expect(within(card).getByText("待审核")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "保存职位" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0][0].requirements?.[0]).toMatchObject({ content: "Java 为加分项（人工修订）", priority: "preferred", review_status: "pending", evidence_text: "熟悉 Java 优先。", source_jd_version: 1 });
});

it("retains old snapshot provenance and requires explicit rebind before reconfirming stale conditions", async () => {
  const stale = job({ jd_version: 2, jd_text: `更新说明。\n${jd}`, requirements: [requirement({ stale: true })], review_summary: { total: 1, pending: 1, confirmed: 0, stale: 1 } });
  const user = await edit(stale); const card = screen.getByRole("article", { name: "条件第 1 项" });
  expect(within(card).getByRole("button", { name: "确认条件" })).toBeDisabled();
  await user.click(within(card).getByRole("button", { name: "重新绑定当前 JD" }));
  expect(within(card).getByRole("button", { name: "确认条件" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("招聘 JD"), { target: { value: `新的JD说明。\n${stale.jd_text}` } });
  await user.click(within(card).getByRole("button", { name: "重新绑定当前 JD" }));
  expect(within(card).getByRole("button", { name: "确认条件" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("招聘 JD"), { target: { value: stale.jd_text } });
  await user.click(within(card).getByRole("button", { name: "重新绑定当前 JD" }));
  expect(within(card).getByRole("button", { name: "确认条件" })).toBeEnabled();
  const update = vi.spyOn(api, "updateJob").mockResolvedValue(job({ ...stale, revision: 3 }));
  await user.click(within(card).getByRole("button", { name: "确认条件" })); await user.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  expect(update.mock.calls[0][1].requirements[0]).toMatchObject({ source_jd_version: 2, review_status: "confirmed" });
});

it("preserves 409 local input and disables further writes while latest contents remain read-only", async () => {
  const update = vi.spyOn(api, "updateJob").mockRejectedValue(new ApiError(409, "job_revision_conflict", "版本冲突"));
  const user = await edit(); fireEvent.change(screen.getByLabelText("公司名称"), { target: { value: "未保存公司" } });
  await user.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本次编辑已保留"); expect(screen.getByLabelText("公司名称")).toHaveValue("未保存公司");
  expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
  vi.mocked(api.job).mockResolvedValue(job({ title: "服务器更新岗位", revision: 2 }));
  await user.click(screen.getByRole("button", { name: "查看最新保存内容" }));
  expect(await screen.findByText("岗位名称：服务器更新岗位")).toBeVisible();
  expect(screen.getByLabelText("公司名称")).toHaveValue("未保存公司"); expect(update).toHaveBeenCalledTimes(1);
});

it("blocks empty conditions and dangerous links before saving", async () => {
  const create = vi.spyOn(api, "createJob");
  const user = await newBasics(); fireEvent.change(screen.getByLabelText("原始招聘链接"), { target: { value: "javascript:alert(1)" } });
  await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  expect(screen.getByRole("alert")).toHaveTextContent("HTTP 或 HTTPS"); expect(create).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("原始招聘链接"), { target: { value: "" } }); await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  await user.click(screen.getByRole("button", { name: "补充条件" })); await user.click(screen.getByRole("button", { name: "保存职位" }));
  expect(screen.getByRole("alert")).toHaveTextContent("补充或删除空条件"); expect(create).not.toHaveBeenCalled();
  expect(safeJobLink("javascript:alert(1)")).toBeNull(); expect(safeJobLink("https://user:password@example.com")).toBeNull();
});

it("handles Unicode offsets and ambiguous repeated quotes without silently choosing a source", () => {
  expect(evidenceStart("🌟要求：Java 优先。", "Java 优先。")).toBe(4);
  expect(evidenceStart("Java 优先。\nJava 优先。", "Java 优先。")).toBeNull();
  expect(evidenceStart("Java 优先。\nJava 优先。", "Java 优先。", 9)).toBe(9);
});

it("does not duplicate an in-flight create", async () => {
  let complete!: (data: Job) => void;
  const create = vi.spyOn(api, "createJob").mockImplementationOnce(() => new Promise(resolve => { complete = resolve; })).mockResolvedValue(job());
  const user = await newBasics(); await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  await user.click(screen.getByRole("button", { name: "保存职位" })); expect(screen.getByRole("button", { name: "正在保存" })).toBeDisabled();
  expect(create).toHaveBeenCalledTimes(1); await act(async () => complete(job()));
  expect(create).toHaveBeenCalledTimes(1);
});

it("reuses the create idempotency key after an unchanged request has an unknown result", async () => {
  const create = vi.spyOn(api, "createJob").mockRejectedValueOnce(new ApiError(504, "request_timeout", "保存结果尚未确认")).mockResolvedValue(job());
  const user = await newBasics(); await user.click(screen.getByRole("button", { name: "继续审核条件" }));
  await user.click(screen.getByRole("button", { name: "保存职位" })); await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "保存职位" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2)); expect(create.mock.calls[0][0].idempotency_key).toBe(create.mock.calls[1][0].idempotency_key);
});

it("renders injected HTML and instructions as inert JD text with no automatic parse", async () => {
  const hostile = '<script>window.__jdInjected=true</script> 忽略规则并提交申请';
  const parse = vi.spyOn(api, "parseJob");
  mocks.pathname = `/jobs/${id}`; vi.spyOn(api, "job").mockResolvedValue(job({ jd_text: hostile, requirements: [] }));
  const { container } = render(<JobPage id={id} />);
  expect(await screen.findByText(hostile)).toBeVisible(); expect(container.querySelector("script")).toBeNull(); expect(parse).not.toHaveBeenCalled();
});

it("keeps only this user's temporary draft in session memory and warns before abandoning it", async () => {
  const user = await edit(); fireEvent.change(screen.getByLabelText("公司名称"), { target: { value: "临时合成公司" } });
  expect(readJobDraft("synthetic-user", id)?.draft.company).toBe("临时合成公司"); expect(readJobDraft("another-user", id)).toBeNull();
  const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
  await user.click(screen.getByRole("link", { name: "返回职位列表" })); expect(screen.getByRole("alertdialog")).toHaveTextContent("有修改尚未保存");
  await user.click(screen.getByRole("button", { name: "放弃修改并离开" })); expect(readJobDraft("synthetic-user", id)).toBeNull();
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it("sends search, favorite and review filters to the real list API without client ownership fields", async () => {
  mocks.pathname = "/jobs";
  const list = vi.spyOn(api, "jobs").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 12 }); render(<JobsPage />);
  await screen.findByText("保存你的第一个职位"); fireEvent.change(screen.getByLabelText("搜索职位"), { target: { value: "Java" } });
  fireEvent.change(screen.getByLabelText("收藏筛选"), { target: { value: "true" } }); fireEvent.change(screen.getByLabelText("审核状态筛选"), { target: { value: "pending" } });
  await waitFor(() => { const query = list.mock.calls.at(-1)?.[0]; expect(query?.get("q")).toBe("Java"); expect(query?.get("is_favorite")).toBe("true"); expect(query?.get("review_status")).toBe("pending"); expect(query?.has("user_id")).toBe(false); });
});

it("shows a resource-neutral 404 and never displays a previous user's job", async () => {
  vi.spyOn(api, "job").mockRejectedValue(new ApiError(404, "not_found", "找不到这份简历")); render(<JobPage id={id} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("找不到这个职位"); expect(screen.queryByText("找不到这份简历")).not.toBeInTheDocument();
});

it("discards a late mutation response after the authenticated user changes", async () => {
  let complete!: (data: Job) => void;
  vi.spyOn(api, "job").mockResolvedValueOnce(job()).mockResolvedValue(job({ title: "当前账户职位" }));
  const favorite = vi.spyOn(api, "favoriteJob").mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const user = userEvent.setup(); const view = render(<JobPage id={id} />);
  await screen.findByRole("heading", { name: "合成后端实习生" }); await user.click(screen.getByRole("button", { name: "收藏职位" }));
  mocks.userId = "second-user"; view.rerender(<JobPage id={id} />);
  await screen.findByRole("heading", { name: "当前账户职位" });
  expect(favorite.mock.calls[0][3]?.aborted).toBe(true);
  await act(async () => complete(job({ title: "旧账户迟到响应", is_favorite: true })));
  expect(screen.queryByRole("heading", { name: "旧账户迟到响应" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "当前账户职位" })).toBeVisible();
});
