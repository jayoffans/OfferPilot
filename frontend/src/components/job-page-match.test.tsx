import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type Application, type CareerProfile, type Job, type JobMatch } from "@/lib/api";
import { JobPage } from "./job-page";
import { JobApplicationsPanel } from "./job-applications";

const mocks = vi.hoisted(() => ({ userId: "match-owner", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => mocks }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "authenticated", user: { id: mocks.userId } }) }));
vi.mock("@/components/workspace-shell", () => ({ WorkspaceShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));

const date = "2026-10-16T00:00:00Z";
const job: Job = {
  id: "22222222-2222-4222-8222-222222222222", title: "合成后端实习", company: null, location: "上海", salary: null, work_type: "internship", source_url: null, source_kind: "pasted", revision: 2, jd_version: 1, is_favorite: false, created_at: date, updated_at: date, review_summary: { total: 1, pending: 0, confirmed: 1, stale: 0 }, schema_version: "1.0", jd_text: "必须熟悉 Python", parser_version: "jd-rules-1.0", jd_snapshots: [],
  requirements: [{ id: "11111111-1111-4111-8111-111111111111", type: "technical_skill", content: "必须熟悉 Python", priority: "must", review_status: "confirmed", evidence_text: "必须熟悉 Python", source_start: 0, source_end: 12, line_start: 1, line_end: 1, source_jd_version: 1, source_kind: "rule", stale: false, revision: 1, schema_version: "1.0", parser_version: "jd-rules-1.0", created_at: date, updated_at: date, reviewed_at: date }],
};
const profile: CareerProfile = { schema_version: "1.0", revision: 3, updated_at: date, education: [], skills: [{ id: "11111111-1111-4111-8111-111111111111", name: "Python", level: "熟悉", source: { kind: "manual", test_result: false, resume_id: null, resume_file_name: null, field_sources: {}, evidence: [] } }], projects: [], experiences: [], candidates: [], completion: { populated_groups: ["skills"], missing_fields: [], pending_count: 0 } };
const match: JobMatch = { id: "33333333-3333-4333-8333-333333333333", job_id: job.id, profile_revision: 3, job_revision: job.revision, jd_version: job.jd_version, rule_version: "match-rules-1.0", schema_version: "1.0", created_at: date, stale: false, stale_reasons: [], evaluation: { schema_version: "1.0", rule_version: "match-rules-1.0", conclusion: "consider", reasons: ["当前已审核条件未发现明确硬性冲突。"], items: [], excluded_requirements: [], next_steps: ["结合岗位职责进一步考虑。"] } };
const application: Application = { id: "44444444-4444-4444-8444-444444444444", job_id: job.id, job_title: job.title, company: null, location: job.location, status: "preparing", attempt_number: 1, submitted_at: null, notes: "", next_action: null, revision: 1, created_at: date, updated_at: date, is_overdue: false, match: null };

beforeEach(() => {
  vi.restoreAllMocks(); vi.clearAllMocks(); mocks.userId = "match-owner";
  vi.spyOn(api, "job").mockResolvedValue(job);
  vi.spyOn(api, "profile").mockResolvedValue(profile);
  vi.spyOn(api, "latestMatch").mockResolvedValue(null);
  vi.spyOn(api, "applications").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 });
});

it("offers the newly saved same-page match for explicit application association without reloading the job", async () => {
  let saved: JobMatch | null = null;
  vi.mocked(api.latestMatch).mockImplementation(async () => saved);
  vi.spyOn(api, "createMatch").mockImplementation(async () => { saved = match; return match; });
  const create = vi.spyOn(api, "createApplication").mockResolvedValue(application);
  const user = userEvent.setup(); render(<JobPage id={job.id} />);
  await screen.findByRole("button", { name: "准备投递" });
  expect(screen.queryByRole("checkbox", { name: /关联这份匹配分析/ })).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "开始匹配" }));
  const associate = await screen.findByRole("checkbox", { name: "关联这份匹配分析（当前依据）" });
  expect(associate).not.toBeChecked(); expect(create).not.toHaveBeenCalled();
  await user.click(associate); await user.click(screen.getByRole("button", { name: "准备投递" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(`/applications/${application.id}`));
  expect(create.mock.calls[0][0]).toMatchObject({ job_id: job.id, job_revision: job.revision, match_id: match.id, reapply: false });
  expect(api.job).toHaveBeenCalledTimes(1);
  expect(api.applications).toHaveBeenCalledTimes(1);
});

it("labels a match with an older local job revision as historical and keeps association optional", async () => {
  vi.mocked(api.latestMatch).mockResolvedValue(match);
  const create = vi.spyOn(api, "createApplication").mockResolvedValue(application);
  const user = userEvent.setup(); render(<JobApplicationsPanel job={{ ...job, revision: job.revision + 1 }} />);
  const associate = await screen.findByRole("checkbox", { name: "关联这份匹配分析（历史结果，依据已变更）" });
  expect(associate).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "准备投递" }));
  await waitFor(() => expect(create).toHaveBeenCalledOnce());
  expect(create.mock.calls[0][0].match_id).toBeNull();
});

it("does not transfer a previous association choice to a newly computed match", async () => {
  vi.mocked(api.latestMatch).mockResolvedValue(match);
  const user = userEvent.setup(); const view = render(<JobApplicationsPanel job={job} />);
  await user.click(await screen.findByRole("checkbox", { name: /关联这份匹配分析/ }));
  expect(screen.getByRole("checkbox")).toBeChecked();
  vi.mocked(api.latestMatch).mockResolvedValue({ ...match, id: "55555555-5555-4555-8555-555555555555" });
  view.rerender(<JobApplicationsPanel job={job} matchRefresh={1} />);
  expect(await screen.findByRole("checkbox", { name: /关联这份匹配分析/ })).not.toBeChecked();
});

it("discards a previous owner's late match read after an account switch", async () => {
  let resolveOld!: (value: JobMatch) => void;
  vi.mocked(api.latestMatch).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const view = render(<JobApplicationsPanel job={job} />);
  await screen.findByRole("button", { name: "准备投递" });
  const oldSignal = vi.mocked(api.latestMatch).mock.calls[0][1]!;
  mocks.userId = "different-owner"; view.rerender(<JobApplicationsPanel job={job} />);
  await waitFor(() => expect(api.latestMatch).toHaveBeenCalledTimes(2));
  expect(oldSignal.aborted).toBe(true);
  await act(async () => resolveOld(match));
  expect(screen.queryByRole("checkbox", { name: /关联这份匹配分析/ })).not.toBeInTheDocument();
});
