import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, clearSession, type CareerProfile, type ProfileCandidate } from "@/lib/api";
import { readProfileDraft } from "@/lib/profile-drafts";
import { ProfilePage } from "./profile-page";
import { validateDraft } from "./profile-editor";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), signOut: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/profile", useRouter: () => mocks }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "authenticated", user: { id: "synthetic-user", email: "synthetic@example.com" }, signOut: mocks.signOut }) }));

const blank: CareerProfile = { schema_version: "1.0", revision: 0, updated_at: null, education: [], skills: [], projects: [], experiences: [], candidates: [], completion: { populated_groups: [], missing_fields: ["education.school", "skills"], pending_count: 0 } };
const manual = { kind: "manual" as const, resume_id: null, resume_file_name: null, field_sources: { name: "manual" as const, school: "manual" as const }, evidence: [] };
function candidate(overrides: Partial<ProfileCandidate> = {}): ProfileCandidate {
  return { id: "11111111-1111-4111-8111-111111111111", category: "skill", value: { name: "Java", level: "熟练" }, status: "pending", evidence_status: "unsupported", evidence: [{ field: "name", source_text: "Not proficient in Java" }], resume_id: "22222222-2222-4222-8222-222222222222", resume_file_name: "synthetic.pdf", schema_version: "1.0", extraction_version: "synthetic-v1", entry_id: null, created_at: "2026-10-11T00:00:00Z", reviewed_at: null, ...overrides };
}
async function open(profile: CareerProfile = blank) { vi.spyOn(api, "profile").mockResolvedValue(profile); render(<ProfilePage />); await screen.findByRole("heading", { name: profile.updated_at ? "资料已保存" : "从一项真实经历开始" }); }
beforeEach(() => { vi.clearAllMocks(); clearSession(); });

it("starts with an empty server profile and keeps all unprovided facts unknown", async () => {
  await open();
  expect(screen.queryByLabelText("学校")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存资料" })).toBeDisabled();
  expect(screen.getByText("目前没有待审核内容")).toBeVisible();
  expect(screen.getByText(/教育经历的学校、技术技能/)).toBeVisible();
});

it("saves a partial manual education using the current revision and no client provenance", async () => {
  const changed: CareerProfile = { ...blank, revision: 1, updated_at: "2026-10-11T00:00:00Z", education: [{ id: "entry-education", school: "合成大学", degree: null, major: null, start_date: null, end_date: null, end_date_kind: null, source: manual }] };
  const save = vi.spyOn(api, "saveProfile").mockResolvedValue(changed);
  const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加教育经历" }));
  await user.type(screen.getByLabelText("学校"), "合成大学");
  await user.click(screen.getByRole("button", { name: "保存资料" }));
  await waitFor(() => expect(screen.getByText(/资料已保存。你可以稍后/)).toBeVisible());
  expect(save).toHaveBeenCalledWith({ revision: 0, education: [{ school: "合成大学", degree: null, major: null, start_date: null, end_date: null, end_date_kind: null }], skills: [], projects: [], experiences: [] }, expect.any(AbortSignal));
  expect(screen.getByLabelText("学历")).toHaveValue("");
  expect(screen.getByLabelText("学校")).toHaveValue("合成大学");
  expect(screen.getByRole("button", { name: "保存资料" })).toBeDisabled();
});

it("explains an empty row and an inconsistent month before making a write", async () => {
  const save = vi.spyOn(api, "saveProfile"); const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加教育经历" }));
  await user.click(screen.getByRole("button", { name: "保存资料" }));
  expect(screen.getByRole("alert")).toHaveTextContent("尚未填写任何内容"); expect(save).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText("学校"), "合成大学");
  fireEvent.change(screen.getByLabelText("入学时间"), { target: { value: "2026-09" } });
  fireEvent.change(screen.getByLabelText("毕业时间"), { target: { value: "2026-06" } });
  await user.click(screen.getByRole("button", { name: "保存资料" }));
  expect(screen.getByRole("alert")).toHaveTextContent("结束时间不能早于开始时间"); expect(save).not.toHaveBeenCalled();
  expect(validateDraft({ education: [{ school: "合成大学", end_date_kind: "expected" }], skills: [], projects: [], experiences: [] })).toContain("恢复为未知");
});

it("a 409 preserves local edits, blocks another write and lets the user inspect latest server content", async () => {
  const save = vi.spyOn(api, "saveProfile").mockRejectedValue(new ApiError(409, "profile_revision_conflict", "资料版本冲突"));
  const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python");
  await user.click(screen.getByRole("button", { name: "保存资料" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本次编辑已保留");
  expect(screen.getByLabelText("技能名称")).toHaveValue("Python"); expect(screen.getByRole("button", { name: "保存资料" })).toBeDisabled();
  vi.mocked(api.profile).mockResolvedValue({ ...blank, revision: 1, skills: [{ id: "server", name: "SQL", level: null, source: manual }] });
  await user.click(screen.getByRole("button", { name: "查看最新保存内容" }));
  expect(await screen.findByText("技能名称：SQL")).toBeVisible(); expect(screen.getByLabelText("技能名称")).toHaveValue("Python"); expect(save).toHaveBeenCalledTimes(1);
});

it("unsupported candidate cannot be confirmed and modifying requires an explicit factual check", async () => {
  const item = candidate();
  const review = vi.spyOn(api, "reviewCandidate").mockResolvedValue({ ...blank, revision: 2, candidates: [{ ...item, status: "modified" }], skills: [{ id: "confirmed", name: "Java", level: "尚在学习", source: { ...manual, kind: "user_modified" } }] });
  const user = userEvent.setup(); await open({ ...blank, revision: 1, candidates: [item], completion: { ...blank.completion, pending_count: 1 } });
  expect(screen.getByRole("button", { name: "确认事实" })).toBeDisabled(); expect(screen.queryByLabelText("技能名称")).not.toBeInTheDocument();
  await user.click(screen.getByText("查看候选来源与证据"));
  expect(screen.getByText("候选引用（未获可靠原文支持）")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "修改后确认" }));
  const dialog = screen.getByRole("dialog");
  await user.clear(within(dialog).getByLabelText("掌握情况")); await user.type(within(dialog).getByLabelText("掌握情况"), "尚在学习");
  expect(within(dialog).getByRole("button", { name: "保存修改并确认" })).toBeDisabled();
  await user.click(within(dialog).getByLabelText("我已核实以上内容符合本人实际情况"));
  await user.click(within(dialog).getByRole("button", { name: "保存修改并确认" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(review).toHaveBeenCalledWith(item.id, { revision: 1, action: "modify", value: { name: "Java", level: "尚在学习" } }, expect.any(AbortSignal));
  expect(screen.getByLabelText("掌握情况")).toHaveValue("尚在学习");
});

it("rejects a supported candidate without adding it to the formal profile, and preserves review evidence", async () => {
  const item = candidate({ evidence_status: "supported", value: { name: "Python", level: null }, evidence: [{ field: "name", source_text: "Python" }] });
  const review = vi.spyOn(api, "reviewCandidate").mockResolvedValue({ ...blank, revision: 2, candidates: [{ ...item, status: "rejected" }] });
  const user = userEvent.setup(); await open({ ...blank, revision: 1, candidates: [item] });
  await user.click(screen.getByRole("button", { name: "拒绝候选" }));
  await screen.findByText("候选已拒绝，未写入正式资料。");
  expect(review).toHaveBeenCalledWith(item.id, { revision: 1, action: "reject" }, expect.any(AbortSignal));
  expect(screen.queryByLabelText("技能名称")).not.toBeInTheDocument();
  await user.click(screen.getByText("已审核记录（1）")); await user.click(screen.getByText("查看审核来源与证据"));
  expect(screen.getByText("已拒绝")).toBeVisible(); expect(screen.getByText("原始候选的文字支持，仅供回顾审核依据")).toBeVisible();
});

it("confirmed candidates become formal facts with server-authored resume evidence", async () => {
  const item = candidate({ evidence_status: "supported", value: { name: "Python", level: null }, evidence: [{ field: "name", source_text: "Python" }] });
  const review = vi.spyOn(api, "reviewCandidate").mockResolvedValue({ ...blank, revision: 2, candidates: [{ ...item, status: "confirmed" }], skills: [{ id: "formal", name: "Python", level: null, source: { kind: "resume_confirmed", resume_id: item.resume_id, resume_file_name: item.resume_file_name, field_sources: { name: "resume" }, evidence: [{ field: "name", source_text: "Python", supports_current_value: true }] } }] });
  const user = userEvent.setup(); await open({ ...blank, revision: 1, candidates: [item] });
  await user.click(screen.getByRole("button", { name: "确认事实" }));
  await screen.findByText("事实已确认并加入正式资料。");
  expect(review).toHaveBeenCalledWith(item.id, { revision: 1, action: "confirm" }, expect.any(AbortSignal));
  expect(screen.getByLabelText("技能名称")).toHaveValue("Python");
  expect(screen.getByLabelText("掌握情况")).toHaveValue("");
  await user.click(screen.getByText("查看来源与证据"));
  expect(screen.getByText("原文文字支持当前值，已由你确认")).toBeVisible();
  await user.type(screen.getByLabelText("掌握情况"), "学习中");
  expect(screen.getByLabelText("掌握情况")).toHaveAccessibleDescription("来源：用户填写 · 待保存");
});

it("shows historical evidence as reference when it no longer supports the current value", async () => {
  const user = userEvent.setup(); await open({ ...blank, updated_at: "2026-10-11T00:00:00Z", skills: [{ id: "skill", name: "Python", level: "学习中", source: { ...manual, kind: "user_modified", evidence: [{ field: "level", source_text: "熟练", supports_current_value: false }] } }] });
  await user.click(screen.getByText("查看来源与证据"));
  expect(screen.getByText("历史原文仅供参考，不代表当前值；当前值由你填写或修订")).toBeVisible();
});

it("warns on reload, navigation and logout while preserving an unsaved draft", async () => {
  const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python");
  const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
  await user.click(screen.getAllByRole("link", { name: "我的简历" })[0]);
  expect(screen.getByRole("alertdialog")).toHaveTextContent("有修改尚未保存"); expect(mocks.push).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "继续编辑" })); expect(screen.getByLabelText("技能名称")).toHaveValue("Python");
  await user.click(screen.getByRole("button", { name: "账户菜单" })); await user.click(screen.getByRole("menuitem", { name: "退出登录" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent("有修改尚未保存"); expect(mocks.signOut).not.toHaveBeenCalled();
});

it("prevents duplicate save while waiting for the actual server result", async () => {
  let complete!: (profile: CareerProfile) => void;
  const save = vi.spyOn(api, "saveProfile").mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python");
  await user.click(screen.getByRole("button", { name: "保存资料" }));
  expect(screen.getByRole("button", { name: "正在保存" })).toBeDisabled(); expect(save).toHaveBeenCalledTimes(1);
  await act(async () => complete({ ...blank, revision: 1, updated_at: "2026-10-11T00:00:00Z", skills: [{ id: "saved", name: "Python", level: null, source: manual }] }));
  expect(screen.getByRole("button", { name: "保存资料" })).toBeDisabled();
});

it("restores unsaved business fields on return in the same session and clears them on session reset", async () => {
  vi.spyOn(api, "profile").mockResolvedValue(blank);
  const user = userEvent.setup(); const first = render(<ProfilePage />);
  await screen.findByRole("heading", { name: "从一项真实经历开始" });
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python");
  first.unmount();
  expect(readProfileDraft("synthetic-user")?.draft.skills[0].name).toBe("Python");
  expect(readProfileDraft("another-user")).toBeNull();
  const second = render(<ProfilePage />);
  expect(await screen.findByLabelText("技能名称")).toHaveValue("Python");
  expect(screen.getByText(/恢复了本次会话尚未保存的编辑/)).toBeVisible();
  clearSession(); second.unmount(); expect(readProfileDraft("synthetic-user")).toBeNull();
  render(<ProfilePage />); await screen.findByRole("heading", { name: "从一项真实经历开始" });
  expect(screen.queryByLabelText("技能名称")).not.toBeInTheDocument();
});

it("returning with an old in-memory draft detects a new revision and keeps the original revision across visits", async () => {
  const getter = vi.spyOn(api, "profile").mockResolvedValue(blank); const user = userEvent.setup();
  const first = render(<ProfilePage />); await screen.findByRole("heading", { name: "从一项真实经历开始" });
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python"); first.unmount();
  getter.mockResolvedValue({ ...blank, revision: 2, skills: [{ id: "server", name: "SQL", level: null, source: manual }] });
  const second = render(<ProfilePage />); expect(await screen.findByLabelText("技能名称")).toHaveValue("Python");
  expect(screen.getByRole("alert")).toHaveTextContent("当前禁止覆盖保存"); expect(screen.getByRole("button", { name: "保存资料" })).toBeDisabled();
  second.unmount(); expect(readProfileDraft("synthetic-user")?.revision).toBe(0);
  render(<ProfilePage />); await screen.findByLabelText("技能名称"); expect(screen.getByRole("alert")).toHaveTextContent("当前禁止覆盖保存");
});

it("explicitly abandoning navigation discards the in-memory draft", async () => {
  const user = userEvent.setup(); await open();
  await user.click(screen.getByRole("button", { name: "添加技能" })); await user.type(screen.getByLabelText("技能名称"), "Python");
  await user.click(screen.getAllByRole("link", { name: "我的简历" })[0]);
  await user.click(screen.getByRole("button", { name: "放弃修改并离开" }));
  expect(mocks.push).toHaveBeenCalledWith("/resumes"); expect(readProfileDraft("synthetic-user")).toBeNull();
});
