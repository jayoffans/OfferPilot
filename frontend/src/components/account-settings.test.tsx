import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, type AccountDeletion, type AccountExport } from "@/lib/api";
import { AccountSettings } from "./account-settings";
import { AccountDeletionStatus } from "./account-deletion";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), reload: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/lib/auth", () => ({ navigateToDeletionStatus: () => mocks.replace("/account-deletion"), useAuth: () => ({ status: "authenticated", user: { id: "synthetic-user", email: "synthetic@example.com", created_at: "2026-10-17T00:00:00Z" }, reload: mocks.reload }) }));
vi.mock("./workspace-shell", () => ({ WorkspaceShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
const id = "11111111-1111-4111-8111-111111111111";
const ready: AccountExport = { id, status: "ready", schema_version: "offerpilot-user-export/1.0", created_at: "2026-10-17T00:00:00Z", updated_at: "2026-10-17T00:01:00Z", expires_at: "2099-10-18T00:00:00Z", completed_at: "2026-10-17T00:01:00Z", byte_size: 1000, sha256: "a".repeat(64), error_code: null, attempt_count: 1, download_available: true };
const completed: AccountDeletion = { id, status: "completed", created_at: "2026-10-17T00:00:00Z", completed_at: "2026-10-17T00:01:00Z", error_code: null, attempt_count: 1 };

beforeEach(() => {
  vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
  vi.spyOn(api, "accountExports").mockResolvedValue([]);
});

it("explains the export scope without creating an export or deleting an account on visit", async () => {
  const create = vi.spyOn(api, "createExport"); const deletion = vi.spyOn(api, "createDeletion");
  render(<AccountSettings />);
  await screen.findByText("还没有导出记录。验证密码后即可申请。");
  expect(screen.getByText("匹配分析")).toBeVisible(); expect(screen.getByText("投递与行动")).toBeVisible();
  expect(screen.getByRole("button", { name: "申请导出" })).toBeDisabled();
  expect(screen.getByLabelText("验证当前密码")).toHaveAttribute("type", "password");
  expect(create).not.toHaveBeenCalled(); expect(deletion).not.toHaveBeenCalled();
});

it("export submission reauthenticates, prevents duplicates and clears the password while waiting", async () => {
  let finish!: (result: AccountExport) => void;
  const create = vi.spyOn(api, "createExport").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const user = userEvent.setup(); render(<AccountSettings />);
  await user.type(screen.getByLabelText("验证当前密码"), "Synthetic-password");
  await user.click(screen.getByRole("button", { name: "申请导出" }));
  expect(screen.getByLabelText("验证当前密码")).toHaveValue("");
  expect(screen.getByRole("button", { name: "正在申请" })).toBeDisabled();
  expect(create.mock.calls[0][0]).toEqual({ password: "Synthetic-password", idempotency_key: expect.any(String) });
  finish(ready);
  await screen.findByRole("button", { name: "下载 ZIP" });
  expect(create).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it("an unknown export result keeps the original request key for an explicit retry", async () => {
  const create = vi.spyOn(api, "createExport").mockRejectedValueOnce(new Error("network fault")).mockResolvedValueOnce(ready);
  const user = userEvent.setup(); render(<AccountSettings />);
  await user.type(screen.getByLabelText("验证当前密码"), "Synthetic-password");
  await user.click(screen.getByRole("button", { name: "申请导出" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("结果可能尚未确认");
  await user.type(screen.getByLabelText("验证当前密码"), "Synthetic-password");
  await user.click(screen.getByRole("button", { name: "申请导出" }));
  await screen.findByRole("button", { name: "下载 ZIP" });
  expect(create.mock.calls[0][0].idempotency_key).toBe(create.mock.calls[1][0].idempotency_key);
});

it("restores server export records and removes a download link when the server reports expiration", async () => {
  vi.spyOn(api, "accountExports").mockResolvedValue([ready]);
  vi.spyOn(api, "downloadExport").mockRejectedValue(new ApiError(410, "export_expired", "导出文件已过期，请重新申请。"));
  const user = userEvent.setup(); render(<AccountSettings />);
  await user.click(await screen.findByRole("button", { name: "下载 ZIP" }));
  await screen.findByText("文件已过期");
  expect(screen.queryByRole("button", { name: "下载 ZIP" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新申请导出" })).toBeDisabled();
});

it("failed exports explain recovery and never claim a complete download", async () => {
  vi.spyOn(api, "accountExports").mockResolvedValue([{ ...ready, status: "failed", error_code: "storage_unavailable", download_available: false }]);
  render(<AccountSettings />);
  await screen.findByText("导出失败");
  expect(screen.getByRole("alert")).toHaveTextContent("尚无可下载文件");
  expect(screen.queryByRole("button", { name: "下载 ZIP" })).not.toBeInTheDocument();
});

async function fillDeletion(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "查看注销说明并继续" }));
  const dialog = screen.getByRole("alertdialog");
  await user.type(within(dialog).getByLabelText("当前密码", { exact: true }), "Synthetic-password");
  await user.type(within(dialog).getByLabelText("输入“注销账户”以确认"), "注销账户");
  return dialog;
}

it("deletion requires password, exact typed confirmation and a separate unchecked acknowledgement", async () => {
  const deletion = vi.spyOn(api, "createDeletion").mockResolvedValue(completed);
  const user = userEvent.setup(); render(<AccountSettings />);
  const dialog = await fillDeletion(user);
  const submit = within(dialog).getByRole("button", { name: "确认注销并删除数据" });
  expect(submit).toBeDisabled(); expect(deletion).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("checkbox"));
  await user.clear(within(dialog).getByLabelText("输入“注销账户”以确认"));
  await user.type(within(dialog).getByLabelText("输入“注销账户”以确认"), "注销");
  fireEvent.submit(dialog.querySelector("form")!);
  expect(deletion).not.toHaveBeenCalled();
  await user.clear(within(dialog).getByLabelText("输入“注销账户”以确认"));
  await user.type(within(dialog).getByLabelText("输入“注销账户”以确认"), "注销账户");
  await user.click(submit);
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/account-deletion"));
  expect(deletion.mock.calls[0][0]).toEqual({ password: "Synthetic-password", confirmation: "注销账户", idempotency_key: expect.any(String) });
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it("an incorrect deletion password leaves the account intact and the dialog can be cancelled", async () => {
  vi.spyOn(api, "createDeletion").mockRejectedValue(new ApiError(403, "reauthentication_failed", "密码验证失败，请重新输入。"));
  const user = userEvent.setup(); render(<AccountSettings />); const dialog = await fillDeletion(user);
  await user.click(within(dialog).getByRole("checkbox"));
  await user.click(within(dialog).getByRole("button", { name: "确认注销并删除数据" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("密码验证失败");
  expect(within(dialog).getByLabelText("当前密码", { exact: true })).toHaveValue("");
  expect(mocks.replace).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "保留账户" }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("a lost deletion response queries safe status before showing the accepted deletion", async () => {
  const deletion = vi.spyOn(api, "createDeletion").mockRejectedValue(new Error("response lost"));
  vi.spyOn(api, "deletionStatus").mockResolvedValue({ ...completed, status: "processing", completed_at: null });
  const user = userEvent.setup(); render(<AccountSettings />); const dialog = await fillDeletion(user);
  await user.click(within(dialog).getByRole("checkbox"));
  await user.click(within(dialog).getByRole("button", { name: "确认注销并删除数据" }));
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/account-deletion"));
  expect(deletion).toHaveBeenCalledTimes(1); expect(api.deletionStatus).toHaveBeenCalledTimes(1);
});

it("deletion status distinguishes incomplete cleanup from completion and preserves backup boundaries", async () => {
  vi.spyOn(api, "deletionStatus").mockResolvedValue({ ...completed, status: "failed", completed_at: null, error_code: "file_cleanup_failed" });
  render(<AccountDeletionStatus />);
  await screen.findByRole("heading", { name: "数据清理尚未完成" });
  expect(screen.getByRole("alert")).toHaveTextContent("需要维护者恢复处理");
  expect(screen.queryByRole("link", { name: "返回登录页" })).not.toBeInTheDocument();
  expect(screen.getByText(/备份副本不会在此刻全部消失/)).toBeVisible();
});

it("completed deletion shows the actual main-storage result and only read-only follow-up", async () => {
  vi.spyOn(api, "deletionStatus").mockResolvedValue(completed);
  render(<AccountDeletionStatus />);
  await screen.findByRole("heading", { name: "账户注销已完成" });
  expect(screen.getByText(/主存储清理已完成/)).toBeVisible();
  expect(screen.getByRole("link", { name: "返回登录页" })).toHaveAttribute("href", "/login");
  expect(screen.queryByRole("button", { name: /注销并删除/ })).not.toBeInTheDocument();
});

it("expired status credentials never imply successful deletion or redirect to login", async () => {
  vi.spyOn(api, "deletionStatus").mockRejectedValue(new ApiError(401, "deletion_status_unavailable", "查询权限已到期"));
  render(<AccountDeletionStatus />);
  expect(await screen.findByRole("alert")).toHaveTextContent("不能据此判断注销是否完成");
  expect(mocks.replace).not.toHaveBeenCalled();
});
