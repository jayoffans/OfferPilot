import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, type CareerProfile, type Job, type JobMatch, type MatchItem } from "@/lib/api";
import { JobMatchPanel } from "./job-match";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "authenticated", user: { id: "synthetic-user" } }) }));
const date = "2026-10-15T00:00:00Z";
const rid = "11111111-1111-4111-8111-111111111111";
const source = { kind: "manual" as const, test_result: false, resume_id: null, resume_file_name: null, field_sources: {}, evidence: [] };
const blank: CareerProfile = { schema_version: "1.0", revision: 0, updated_at: null, education: [], skills: [], projects: [], experiences: [], candidates: [], completion: { populated_groups: [], missing_fields: [], pending_count: 0 } };
const profile: CareerProfile = { ...blank, revision: 3, skills: [{ id: rid, name: "Python 3", level: "熟悉", source }] };
const job: Job = { id: "22222222-2222-4222-8222-222222222222", title: "合成后端实习", company: null, location: "上海", salary: null, work_type: "internship", source_url: null, source_kind: "pasted", revision: 2, jd_version: 1, is_favorite: false, created_at: date, updated_at: date, review_summary: { total: 1, pending: 0, confirmed: 1, stale: 0 }, schema_version: "1.0", jd_text: "必须熟悉 Python", requirements: [{ id: rid, type: "technical_skill", content: "必须熟悉 Python", priority: "must", review_status: "confirmed", evidence_text: "必须熟悉 Python", source_start: 0, source_end: 12, line_start: 1, line_end: 1, source_jd_version: 1, source_kind: "rule", stale: false, revision: 1, schema_version: "1.0", parser_version: "jd-rules-1.0", created_at: date, updated_at: date, reviewed_at: date }], parser_version: "jd-rules-1.0", jd_snapshots: [] };
const item: MatchItem = { requirement_id: rid, type: "technical_skill", content: "必须熟悉 Python", priority: "must", group: "skills", scope: "qualification", status: "met", reason: "正式技能 Python 3 可支持 Python 要求。", profile_evidence: [{ entry_id: rid, category: "skill", field: "name", value: "Python 3", source_kind: "manual" }], jd_evidence: { evidence_text: "必须熟悉 Python", source_jd_version: 1, source_start: 0, source_end: 12, line_start: 1, line_end: 1, source_kind: "rule" }, rule_version: "match-rules-1.0", verification: [] };
const result: JobMatch = { id: "33333333-3333-4333-8333-333333333333", job_id: job.id, profile_revision: 3, job_revision: 2, jd_version: 1, rule_version: "match-rules-1.0", schema_version: "1.0", created_at: date, stale: false, stale_reasons: [], evaluation: { schema_version: "1.0", rule_version: "match-rules-1.0", conclusion: "consider", reasons: ["当前已审核条件未发现明确硬性冲突。"], items: [item], excluded_requirements: [], next_steps: ["结合岗位职责进一步考虑。"] } };
beforeEach(() => { vi.restoreAllMocks(); vi.spyOn(api, "profile").mockResolvedValue(profile); vi.spyOn(api, "latestMatch").mockResolvedValue(null); });

it("requires formal facts and reviewed current requirements before calculation", async () => {
  vi.mocked(api.profile).mockResolvedValue(blank);
  const create = vi.spyOn(api, "createMatch");
  const review = vi.fn(); render(<JobMatchPanel job={{ ...job, requirements: [] }} onReview={review} />);
  expect(await screen.findByText(/还没有已确认的求职资料/)).toBeVisible();
  expect(screen.getByText(/暂无当前 JD 的已审核条件/)).toBeVisible();
  expect(screen.getByRole("button", { name: "开始匹配" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "去审核 JD 条件" }));
  expect(review).toHaveBeenCalledOnce(); expect(create).not.toHaveBeenCalled();
});

it("sends only revisions and an idempotency key, then exposes both evidence sources without a score", async () => {
  const create = vi.spyOn(api, "createMatch").mockResolvedValue(result);
  const user = userEvent.setup(); render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "开始匹配" }));
  expect(await screen.findByRole("heading", { name: "可以进一步考虑" })).toBeVisible();
  expect(create).toHaveBeenCalledWith(job.id, { profile_revision: 3, job_revision: 2, idempotency_key: expect.any(String) }, expect.any(AbortSignal));
  const condition = screen.getByRole("article", { name: "匹配条件：必须熟悉 Python" });
  expect(within(condition).getByText("明确满足")).toBeVisible();
  await user.click(within(condition).getByText("查看判断与证据"));
  expect(within(condition).getByText("Python 3")).toBeVisible();
  expect(within(condition).getByText(/JD v1 · 第 1 行 · 字符 0—12/)).toBeVisible();
  expect(within(condition).getByText(/用户填写/)).toBeVisible();
  expect(screen.getByText(/不是录用概率/)).toBeVisible();
});

it("shows unknown evidence and preferred conditions without inventing a negative fact", async () => {
  vi.mocked(api.latestMatch).mockResolvedValue({ ...result, evaluation: { ...result.evaluation, conclusion: "verify_first", items: [{ ...item, content: "Java 优先", priority: "preferred", status: "unknown", reason: "正式画像没有已确认的 Java 技能记录。", profile_evidence: [], verification: ["如实际具备，可补充确认技能。"] }], excluded_requirements: [{ requirement_id: rid, content: "未审核要求", reason: "pending_review" }] } });
  render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  await screen.findByRole("heading", { name: "建议先核实关键条件" });
  expect(screen.getByText("优先条件")).toBeVisible(); expect(screen.getByText("未发现证据")).toBeVisible();
  await userEvent.click(screen.getByText("查看判断与证据"));
  expect(screen.getByText(/缺少记录不代表你不具备该能力/)).toBeVisible();
  expect(screen.getByText("如实际具备，可补充确认技能。")).toBeVisible();
  await userEvent.click(screen.getByText("未纳入正式匹配的条件（1 项）"));
  expect(screen.getByText(/尚未审核确认/)).toBeVisible();
});

it("labels stale results as historical and recomputes against the refreshed profile revision", async () => {
  vi.mocked(api.profile).mockResolvedValue({ ...profile, revision: 4 });
  vi.mocked(api.latestMatch).mockResolvedValue({ ...result, stale: true, stale_reasons: ["profile_changed"] });
  const create = vi.spyOn(api, "createMatch").mockResolvedValue({ ...result, profile_revision: 4, evaluation: { ...result.evaluation, conclusion: "hard_conflict", reasons: ["毕业年份与必须届别冲突。"] } });
  render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  expect(await screen.findByText("匹配依据已变更")).toBeVisible(); expect(screen.getByText(/历史综合建议/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "重新计算匹配" }));
  expect(await screen.findByRole("heading", { name: "存在明确硬性条件冲突" })).toBeVisible();
  expect(create.mock.calls[0][1].profile_revision).toBe(4); expect(screen.queryByText("匹配依据已变更")).not.toBeInTheDocument();
});

it("keeps uncertain POST attempts idempotent after a read-only refresh and prevents double click", async () => {
  let reject!: (cause: unknown) => void;
  const create = vi.spyOn(api, "createMatch").mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; })).mockResolvedValue(result);
  const user = userEvent.setup(); render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "开始匹配" }));
  expect(screen.getByRole("button", { name: "正在逐项匹配" })).toBeDisabled();
  await act(async () => { reject(new ApiError(504, "timeout", "结果尚未确认。")); });
  expect(await screen.findByRole("alert")).toHaveTextContent("先刷新匹配状态");
  await user.click(screen.getByRole("button", { name: "刷新匹配状态" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "开始匹配" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "开始匹配" }));
  await screen.findByRole("heading", { name: "可以进一步考虑" });
  expect(create).toHaveBeenCalledTimes(2); expect(create.mock.calls[1][1].idempotency_key).toBe(create.mock.calls[0][1].idempotency_key);
});

it("handles read failures and offers a bounded explicit refresh without posting automatically", async () => {
  vi.mocked(api.latestMatch).mockRejectedValue(new ApiError(503, "unavailable", "服务暂时不可用。"));
  const create = vi.spyOn(api, "createMatch"); render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
  expect(screen.getByRole("button", { name: "开始匹配" })).toBeDisabled();
  expect(create).not.toHaveBeenCalled(); expect(api.latestMatch).toHaveBeenCalledTimes(1);
});

it("starts a new idempotent intent after a rule change and retains it when the new calculation is uncertain", async () => {
  const updated: JobMatch = { ...result, id: "44444444-4444-4444-8444-444444444444", rule_version: "match-rules-2.0", evaluation: { ...result.evaluation, rule_version: "match-rules-2.0" } };
  const create = vi.spyOn(api, "createMatch").mockResolvedValueOnce(result).mockRejectedValueOnce(new ApiError(504, "timeout", "结果尚未确认。")).mockResolvedValueOnce(updated);
  const user = userEvent.setup(); render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "开始匹配" }));
  await screen.findByRole("heading", { name: "可以进一步考虑" });
  const previousKey = create.mock.calls[0][1].idempotency_key;
  vi.mocked(api.latestMatch).mockResolvedValue({ ...result, stale: true, stale_reasons: ["rule_changed"] });
  await user.click(screen.getByRole("button", { name: "刷新匹配状态" }));
  expect(await screen.findByText("匹配依据已变更")).toBeVisible();
  expect(create).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "重新计算匹配" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("先刷新匹配状态");
  const changedRulesKey = create.mock.calls[1][1].idempotency_key;
  expect(changedRulesKey).not.toBe(previousKey);
  await user.click(screen.getByRole("button", { name: "刷新匹配状态" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "重新计算匹配" })).toBeEnabled());
  expect(create).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole("button", { name: "重新计算匹配" }));
  await waitFor(() => expect(screen.queryByText("匹配依据已变更")).not.toBeInTheDocument());
  expect(create).toHaveBeenCalledTimes(3);
  expect(create.mock.calls[2][1].idempotency_key).toBe(changedRulesKey);
  expect(screen.getByText(/规则 match-rules-2.0/)).toBeVisible();
});

it("shows preference evidence in Chinese with explicit salary units and preserves unsupported snapshots", async () => {
  const salary = (min: number | null, max: number | null, period = "day", tax = "pre_tax") => JSON.stringify({ min, max, currency: "CNY", period, tax });
  vi.mocked(api.latestMatch).mockResolvedValue({ ...result, evaluation: { ...result.evaluation, items: [{ ...item, group: "preferences", scope: "preference", content: "个人求职偏好", profile_evidence: [
    { entry_id: null, category: "preference", field: "salary", value: salary(250, 350), source_kind: "manual" },
    { entry_id: null, category: "preference", field: "salary", value: salary(12000, null, "month", "after_tax"), source_kind: "manual" },
    { entry_id: null, category: "preference", field: "salary", value: salary(null, 200000, "year", "unknown"), source_kind: "manual" },
    { entry_id: null, category: "preference", field: "work_types", value: "internship、campus、full_time", source_kind: "manual" },
    { entry_id: null, category: "preference", field: "salary", value: "无法解析的历史薪资依据", source_kind: "manual" },
  ] }] } });
  render(<JobMatchPanel job={job} onReview={vi.fn()} />);
  await screen.findByRole("heading", { name: "可以进一步考虑" });
  await userEvent.click(screen.getByText("查看判断与证据"));
  expect(screen.getByText("税前 250—350 元/天（人民币）")).toBeVisible();
  expect(screen.getByText("税后 12000 元/月（人民币，上限未填写）")).toBeVisible();
  expect(screen.getByText("税费口径未明确 200000 元/年（人民币，下限未填写）")).toBeVisible();
  expect(screen.getByText("实习、校招、全职")).toBeVisible();
  expect(screen.getByText("无法解析的历史薪资依据")).toBeVisible();
  expect(screen.queryByText(/\{"min":/)).not.toBeInTheDocument();
});
