import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, type CareerProfile } from "@/lib/api";
import { emptyPreferences, ProfilePreferencesEditor } from "./profile-preferences";

const profile: CareerProfile = { schema_version: "1.0", revision: 3, updated_at: null, education: [], skills: [], projects: [], experiences: [], candidates: [], completion: { populated_groups: [], missing_fields: [], pending_count: 0 }, preferences: emptyPreferences };
beforeEach(() => vi.restoreAllMocks());
function open(disabled = false) {
  const callbacks = { onDirty: vi.fn(), onBusy: vi.fn(), onSaved: vi.fn(), onFailure: vi.fn() };
  render(<ProfilePreferencesEditor profile={profile} disabled={disabled} {...callbacks} />);
  return callbacks;
}
it("saves only the minimal preference document against the current profile revision", async () => {
  const saved = { ...profile, revision: 4, preferences: { ...emptyPreferences, locations: ["上海", "杭州"], work_types: ["internship" as const], target_roles: ["后端开发"], salary: { min: 200, max: 300, currency: "CNY" as const, period: "day" as const, tax: "pre_tax" as const }, weekly_days: 3, internship_months: 6 } };
  const save = vi.spyOn(api, "savePreferences").mockResolvedValue(saved);
  const callbacks = open(); const user = userEvent.setup();
  await user.type(screen.getByLabelText("意向城市"), "上海、杭州、上海");
  await user.type(screen.getByLabelText("岗位方向"), "后端开发"); await user.click(screen.getByLabelText("实习", { exact: true }));
  fireEvent.change(screen.getByLabelText("期望薪资下限（人民币元）"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("期望薪资上限（人民币元）"), { target: { value: "300" } });
  await user.selectOptions(screen.getByLabelText("薪资周期"), "day"); await user.selectOptions(screen.getByLabelText("薪资税费口径"), "pre_tax");
  fireEvent.change(screen.getByLabelText("每周可实习天数"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("可实习月数"), { target: { value: "6" } });
  await user.click(screen.getByRole("button", { name: "保存求职偏好" }));
  await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledWith(saved));
  expect(save).toHaveBeenCalledWith({ revision: 3, preferences: saved.preferences }, expect.any(AbortSignal));
  expect(callbacks.onDirty).toHaveBeenCalledWith(true);
});
it("keeps unknown preferences empty and rejects reverse salary ranges without a write", async () => {
  const save = vi.spyOn(api, "savePreferences"); open();
  expect(screen.getByRole("button", { name: "保存求职偏好" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("期望薪资下限（人民币元）"), { target: { value: "300" } });
  fireEvent.change(screen.getByLabelText("期望薪资上限（人民币元）"), { target: { value: "200" } });
  await userEvent.click(screen.getByRole("button", { name: "保存求职偏好" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("下限不能高于上限"); expect(save).not.toHaveBeenCalled();
});
it("preserves the user's input after a revision conflict", async () => {
  const conflict = new ApiError(409, "profile_revision_conflict", "资料已更新");
  vi.spyOn(api, "savePreferences").mockRejectedValue(conflict); const callbacks = open();
  await userEvent.type(screen.getByLabelText("意向城市"), "上海"); await userEvent.click(screen.getByRole("button", { name: "保存求职偏好" }));
  await waitFor(() => expect(callbacks.onFailure).toHaveBeenCalledWith(conflict));
  expect(screen.getByLabelText("意向城市")).toHaveValue("上海");
});
it("does not allow a preference write while another profile edit is pending", async () => {
  const save = vi.spyOn(api, "savePreferences"); open(true);
  expect(screen.getByLabelText("意向城市")).toBeDisabled(); expect(screen.getByRole("button", { name: "保存求职偏好" })).toBeDisabled();
  expect(save).not.toHaveBeenCalled();
});
