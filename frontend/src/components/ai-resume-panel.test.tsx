import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, SESSION_RESET_EVENT, type KeyStatus, type ExtractionPreview, type ExtractionRun } from "@/lib/api";
import { AiResumePanel } from "./ai-resume-panel";

const id = "22222222-2222-4222-8222-222222222222";
const configured: KeyStatus = { configured: true, enabled: true, ttl_seconds: 3600, provider: "deepseek", expires_at: "2099-10-12T02:00:00Z", unavailable_reason: null };
const preview: ExtractionPreview = { authorization_id: "11111111-1111-4111-8111-111111111111", source_version: "a".repeat(64), preview_version: "deepseek-consent-v1", provider: "deepseek", model: "deepseek-flash", redacted_text: "*****\n技术技能\nPython", sent_text_sha256: "b".repeat(64), max_calls: 1, max_input_tokens: 18000, max_output_tokens: 4096, redaction_version: "resume-redaction-v1", privacy_notice: "会发送给外部服务商，仍可能保留及训练", cost_notice: "最多1次，API费用由用户承担，超时仍可能收费", expires_at: "2099-10-12T02:00:00Z" };
const run: ExtractionRun = { id: "33333333-3333-4333-8333-333333333333", resume_id: id, status: "queued", mode: "deepseek", test_result: false, revision: 0, source_version: preview.source_version, attempt_count: 0, max_attempts: 1, call_count: 0, max_calls: 1, candidate_count: 0, schema_version: "2.0", extraction_version: "synthetic-deepseek-v2", prompt_version: "resume_extract_v2", provider_version: "synthetic-no-network", error_code: null, created_at: "2026-10-12T00:00:00Z", updated_at: "2026-10-12T00:00:00Z", finished_at: null, model: "deepseek-flash", billing_status: "not_sent", prompt_tokens: null, completion_tokens: null, total_tokens: null };
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "deepseekKey").mockResolvedValue(configured);
  vi.spyOn(api, "resumes").mockResolvedValue([{ id, file_name: "合成.pdf", file_type: "application/pdf", created_at: "2026-10-12T00:00:00Z" }]);
  vi.spyOn(api, "extraction").mockResolvedValue({ enabled: true, mode: "deepseek", test_result: false, source_version: preview.source_version, recent_runs: [] });
});
afterEach(() => vi.useRealTimers());

it("browsing and choosing a resume never creates a paid run", async () => {
  const create = vi.spyOn(api, "createExtraction");
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  expect(create).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "开始 AI 解析" })).not.toBeInTheDocument();
});

it("no key disables preview and key input clears before submission response", async () => {
  vi.spyOn(api, "deepseekKey").mockResolvedValue({ ...configured, configured: false, expires_at: null });
  const configure = vi.spyOn(api, "configureDeepseekKey").mockResolvedValue(configured);
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  expect(screen.getByRole("button", { name: "预览发送内容与费用" })).toBeDisabled();
  const input = screen.getByLabelText("DeepSeek API Key");
  expect(input).toHaveAttribute("type", "password");
  await userEvent.type(input, "synthetic-private-key");
  await userEvent.click(screen.getByRole("button", { name: "配置会话 Key" }));
  expect(input).toHaveValue("");
  expect(configure).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it("requires distinct preview and unchecked consent before the only paid POST", async () => {
  vi.spyOn(api, "extractionPreview").mockResolvedValue(preview);
  const create = vi.spyOn(api, "createExtraction").mockRejectedValue(new Error("synthetic network fault"));
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  await userEvent.click(screen.getByRole("button", { name: "预览发送内容与费用" }));
  const start = await screen.findByRole("button", { name: "开始 AI 解析" });
  expect(start).toBeDisabled(); expect(create).not.toHaveBeenCalled();
  expect(screen.getByLabelText("将发送的脱敏简历文本")).toHaveTextContent("Python");
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(start);
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0][1]).toMatchObject({ mode: "deepseek", authorization: { authorization_id: preview.authorization_id, accepted: true, max_calls: 1 } });
  await screen.findByRole("alert");
  expect(create).toHaveBeenCalledTimes(1);
});

it("revocation and session reset clear transient credentials and consent", async () => {
  const revoke = vi.spyOn(api, "revokeDeepseekKey").mockResolvedValue({ ...configured, configured: false, expires_at: null });
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("button", { name: "撤销 Key" });
  await userEvent.click(screen.getByRole("button", { name: "撤销 Key" }));
  expect(revoke).toHaveBeenCalledTimes(1);
  await userEvent.type(screen.getByLabelText("DeepSeek API Key"), "synthetic-transient");
  act(() => window.dispatchEvent(new Event(SESSION_RESET_EVENT)));
  expect(screen.getByLabelText("DeepSeek API Key")).toHaveValue("");
});

it("manual refresh recovers failed initial metadata without selecting a resume or sending a paid POST", async () => {
  const keys = vi.mocked(api.deepseekKey).mockRejectedValueOnce(new Error("synthetic offline"));
  const create = vi.spyOn(api, "createExtraction");
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("alert");
  expect(screen.getByLabelText("DeepSeek API Key")).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "刷新任务状态" }));
  await screen.findByRole("option", { name: "合成.pdf" });
  expect(screen.getByLabelText("DeepSeek API Key")).toBeEnabled();
  expect(keys).toHaveBeenCalledTimes(2); expect(api.resumes).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(create).not.toHaveBeenCalled();
});

it("manual task refresh updates a key revoked elsewhere and disables preview", async () => {
  const create = vi.spyOn(api, "createExtraction");
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  expect(screen.getByRole("button", { name: "预览发送内容与费用" })).toBeEnabled();
  vi.mocked(api.deepseekKey).mockResolvedValue({ ...configured, configured: false, expires_at: null });
  await userEvent.click(screen.getByRole("button", { name: "刷新任务状态" }));
  await screen.findByText("当前会话未配置 Key");
  expect(screen.getByRole("button", { name: "预览发送内容与费用" })).toBeDisabled();
  expect(api.deepseekKey).toHaveBeenCalledTimes(2); expect(create).not.toHaveBeenCalled();
});

it("key_unavailable reads authoritative key status and clears consent without retrying a POST", async () => {
  vi.spyOn(api, "extractionPreview").mockRejectedValue(new ApiError(409, "key_unavailable", "当前会话 Key 不可用，请重新配置后授权"));
  const create = vi.spyOn(api, "createExtraction");
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  vi.mocked(api.deepseekKey).mockResolvedValue({ ...configured, configured: false, expires_at: null });
  await userEvent.click(screen.getByRole("button", { name: "预览发送内容与费用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请重新配置后授权");
  await screen.findByText("当前会话未配置 Key");
  expect(api.deepseekKey).toHaveBeenCalledTimes(2); expect(api.extractionPreview).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "预览发送内容与费用" })).toBeDisabled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(create).not.toHaveBeenCalled();
});

it("explains the polling limit and manual refresh resumes only reads", async () => {
  vi.useFakeTimers();
  const create = vi.spyOn(api, "createExtraction");
  vi.mocked(api.extraction).mockResolvedValue({ enabled: true, mode: "deepseek", test_result: false, source_version: preview.source_version, recent_runs: [run] });
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await act(async () => {});
  fireEvent.change(screen.getByLabelText("选择已上传的简历"), { target: { value: id } });
  await act(async () => { await vi.advanceTimersByTimeAsync(69_000); });
  expect(api.extraction).toHaveBeenCalledTimes(70);
  expect(screen.getByText(/自动查询已达到本次上限/)).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.extraction).toHaveBeenCalledTimes(70);
  vi.mocked(api.extraction).mockResolvedValue({ enabled: true, mode: "deepseek", test_result: false, source_version: preview.source_version, recent_runs: [{ ...run, status: "succeeded", candidate_count: 4 }] });
  fireEvent.click(screen.getByRole("button", { name: "刷新任务状态" }));
  await act(async () => {});
  expect(screen.getByText("候选已生成")).toBeVisible();
  expect(screen.queryByText(/自动查询已达到本次上限/)).not.toBeInTheDocument();
  expect(api.deepseekKey).toHaveBeenCalledTimes(2); expect(create).not.toHaveBeenCalled();
});

it("session reset aborts a task read and discards its late response", async () => {
  let signal: AbortSignal | undefined;
  let complete!: (value: Awaited<ReturnType<typeof api.extraction>>) => void;
  vi.mocked(api.extraction).mockImplementation((_id, current) => { signal = current; return new Promise(resolve => { complete = resolve; }); });
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  expect(signal).toBeDefined();
  act(() => window.dispatchEvent(new Event(SESSION_RESET_EVENT)));
  expect(signal?.aborted).toBe(true);
  await act(async () => complete({ enabled: true, mode: "deepseek", test_result: false, source_version: preview.source_version, recent_runs: [run] }));
  expect(screen.getByLabelText("选择已上传的简历")).toHaveValue("");
  expect(screen.queryByText("等待处理")).not.toBeInTheDocument();
});

it("reports token bounds failure with a billing check instead of retrying", async () => {
  const create = vi.spyOn(api, "createExtraction");
  vi.mocked(api.extraction).mockResolvedValue({ enabled: true, mode: "deepseek", test_result: false, source_version: preview.source_version, recent_runs: [{ ...run, status: "failed", error_code: "provider_usage_exceeded_bounds" }] });
  render(<AiResumePanel onCandidates={vi.fn()} disabled={false} />);
  await screen.findByRole("option", { name: "合成.pdf" });
  await userEvent.selectOptions(screen.getByLabelText("选择已上传的简历"), id);
  expect(await screen.findByRole("alert")).toHaveTextContent("请先核对 DeepSeek 账单并联系负责人");
  expect(create).not.toHaveBeenCalled();
});
