import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, type ExtractionCapability, type ExtractionRun } from "@/lib/api";
import { ResumeExtraction } from "./resume-extraction";

const id = "22222222-2222-4222-8222-222222222222";
const run: ExtractionRun = { id: "11111111-1111-4111-8111-111111111111", resume_id: id, status: "queued", mode: "mock", test_result: true, billing_status: "not_sent", revision: 2, attempt_count: 0, max_attempts: 2, call_count: 0, max_calls: 4, candidate_count: 0, error_code: null, created_at: "2026-10-12T00:00:00Z", updated_at: "2026-10-12T00:00:00Z", finished_at: null, source_version: "a".repeat(64), schema_version: "2.0", extraction_version: "mock-v2", prompt_version: "resume_extract_v2", provider_version: "mock-v2" };
const capability: ExtractionCapability = { enabled: true, mode: "mock", test_result: true, source_version: run.source_version, recent_runs: [] };
beforeEach(() => vi.restoreAllMocks());

it("does not expose Mock actions or contact extraction API in the default UI", () => {
  const read = vi.spyOn(api, "extraction");
  render(<ResumeExtraction id={id} enabled={false} canRun />);
  expect(screen.queryByRole("heading", { name: /Mock/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /创建/ })).not.toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
});

it("requires both backend opt-in and an available parsed resume", async () => {
  vi.spyOn(api, "extraction").mockResolvedValue({ ...capability, enabled: false, mode: "disabled", test_result: false });
  const create = vi.spyOn(api, "createExtraction");
  render(<ResumeExtraction id={id} enabled canRun={false} />);
  expect(await screen.findByRole("button", { name: "创建 Mock 测试任务" })).toBeDisabled();
  expect(screen.getByText(/后端 Mock 测试模式未启用/)).toBeVisible();
  expect(screen.getByText(/简历须完成文本解析/)).toBeVisible(); expect(create).not.toHaveBeenCalled();
});

it("creates one explicit test task with the server version and prevents double submission", async () => {
  vi.spyOn(api, "extraction").mockResolvedValue(capability);
  let complete!: (value: ExtractionRun) => void;
  const create = vi.spyOn(api, "createExtraction").mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const user = userEvent.setup(); render(<ResumeExtraction id={id} enabled canRun />);
  await user.click(await screen.findByRole("button", { name: "创建 Mock 测试任务" }));
  expect(screen.getByRole("button", { name: "正在创建测试任务" })).toBeDisabled();
  expect(create).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledWith(id, { idempotency_key: expect.any(String), source_version: run.source_version, mode: "mock" }, expect.any(AbortSignal));
  vi.mocked(api.extraction).mockResolvedValue({ ...capability, recent_runs: [{ ...run, status: "succeeded", candidate_count: 3 }] });
  await act(async () => complete({ ...run, status: "succeeded", candidate_count: 3 }));
  expect(await screen.findByText("候选已生成")).toBeVisible();
  expect(screen.getByText(/3 项测试候选已保存，仍待用户审核/)).toBeVisible();
  expect(screen.getByRole("link", { name: "查看并审核候选" })).toHaveAttribute("href", "/profile");
  expect(screen.getByText(/不是真实 AI 分析/)).toBeVisible();
});

it("cancels with the actual run revision and displays the persistent cancelled state", async () => {
  vi.spyOn(api, "extraction").mockResolvedValue({ ...capability, recent_runs: [run] });
  const cancel = vi.spyOn(api, "cancelExtraction").mockResolvedValue({ ...run, status: "cancelled", revision: 3 });
  const user = userEvent.setup(); render(<ResumeExtraction id={id} enabled canRun />);
  await screen.findByText("等待处理");
  vi.mocked(api.extraction).mockResolvedValue({ ...capability, recent_runs: [{ ...run, status: "cancelled", revision: 3 }] });
  await user.click(screen.getByRole("button", { name: "取消测试任务" }));
  expect(await screen.findByText("已取消")).toBeVisible();
  expect(cancel).toHaveBeenCalledWith(run.id, 2, expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: "有限重试测试任务" })).not.toBeInTheDocument();
});

it("shows a conflict safely and only permits retry while budgets remain", async () => {
  const failed = { ...run, status: "failed" as const, attempt_count: 1, error_code: "schema_invalid" };
  vi.spyOn(api, "extraction").mockResolvedValue({ ...capability, recent_runs: [failed] });
  const retry = vi.spyOn(api, "retryExtraction").mockRejectedValue(new ApiError(409, "run_conflict", "任务冲突"));
  const user = userEvent.setup(); const view = render(<ResumeExtraction id={id} enabled canRun />);
  await user.click(await screen.findByRole("button", { name: "有限重试测试任务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请刷新任务状态后再操作");
  expect(retry).toHaveBeenCalledTimes(1);
  expect(retry).toHaveBeenCalledWith(run.id, 2, expect.any(AbortSignal));
  view.unmount();
  vi.mocked(api.extraction).mockResolvedValue({ ...capability, recent_runs: [{ ...failed, attempt_count: 2 }] });
  render(<ResumeExtraction id={id} enabled canRun />); await screen.findByText("抽取失败");
  expect(screen.queryByRole("button", { name: "有限重试测试任务" })).not.toBeInTheDocument();
});

it("aborts in-flight state requests on unmount so stale responses cannot update another page", async () => {
  let signal: AbortSignal | undefined;
  vi.spyOn(api, "extraction").mockImplementation((_id, current) => { signal = current; return new Promise(() => undefined); });
  const view = render(<ResumeExtraction id={id} enabled canRun />);
  await waitFor(() => expect(signal).toBeDefined()); view.unmount(); expect(signal?.aborted).toBe(true);
});
