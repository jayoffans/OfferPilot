import { test, expect, control, createAccount, loginUI, uploadUI, logoutUI } from "./fixtures";
import { mkdir } from "node:fs/promises";

test("会话Key、独立授权、四类候选、主动确认与刷新恢复（免费Provider注入）", async ({ page, request }) => {
  await control("ai-mode", { mode: "default" });
  const account = await createAccount(request); await loginUI(page, account.email);
  const pdf = Buffer.from(await (await control("extraction-pdf")).arrayBuffer());
  const resume = await uploadUI(page, pdf, "BYOK合成验收.pdf");
  await page.goto("/profile");
  await expect(page.getByRole("heading", { name: "AI 简历解析" })).toBeVisible();
  await page.getByLabel("选择已上传的简历").selectOption(resume.id);
  const previewButton = page.getByRole("button", { name: "预览发送内容与费用" });
  await expect(previewButton).toBeDisabled();
  await page.getByLabel("DeepSeek API Key").fill("synthetic-e2e-key-no-network");
  await page.getByRole("button", { name: "配置会话 Key" }).click();
  await expect(page.getByLabel("DeepSeek API Key")).toHaveValue("");
  await expect(previewButton).toBeEnabled();
  await previewButton.click();
  const dialog = page.getByRole("dialog", { name: "授权本次 AI 解析" });
  await expect(dialog.getByRole("button", { name: "开始 AI 解析" })).toBeDisabled();
  await expect(dialog.getByLabel("将发送的脱敏简历文本")).toContainText("Python");
  const before = await page.request.get(`/api/v1/resumes/${resume.id}/extraction`);
  expect((await before.json()).recent_runs).toEqual([]);
  await mkdir("artifacts/screenshots", { recursive: true });
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `artifacts/screenshots/byok-consent-${width}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "开始 AI 解析" }).click();
  await expect(page.getByText("候选已生成", { exact: true })).toBeVisible();
  const profileBefore = await (await page.request.get("/api/v1/profile")).json();
  expect(profileBefore.skills).toEqual([]); expect(profileBefore.education).toEqual([]);
  expect(new Set(profileBefore.candidates.map((item: {category: string}) => item.category))).toEqual(new Set(["education", "skill", "project", "experience"]));
  await page.getByRole("button", { name: "载入待审核候选" }).click();
  const python = page.locator(".candidate-card").filter({ has: page.locator(".candidate-value", { hasText: /^技能名称：Python/ }) });
  await expect(python).toHaveCount(1); await python.getByRole("button", { name: "确认事实", exact: true }).click();
  await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("Python");
  await page.reload();
  await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("Python");
  await page.getByLabel("选择已上传的简历").selectOption(resume.id);
  await expect(page.getByText("候选已生成", { exact: true })).toBeVisible();
  const state = await (await page.request.get(`/api/v1/resumes/${resume.id}/extraction`)).json();
  expect(state.recent_runs[0].call_count).toBe(1); expect(state.recent_runs[0].max_calls).toBe(1);
  await logoutUI(page); await loginUI(page, account.email); await page.goto("/profile");
  await expect(page.getByText("当前会话未配置 Key", { exact: true })).toBeVisible();
  await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("Python");
});

test("网络未知收费状态不会自动重试，Key可主动撤销（免费故障注入）", async ({ page, request }) => {
  await control("ai-mode", { mode: "timeout" });
  try {
    const account = await createAccount(request); await loginUI(page, account.email);
    const pdf = Buffer.from(await (await control("extraction-pdf")).arrayBuffer());
    const resume = await uploadUI(page, pdf, "BYOK网络故障合成.pdf");
    await page.goto("/profile"); await page.getByLabel("选择已上传的简历").selectOption(resume.id);
    await page.getByLabel("DeepSeek API Key").fill("synthetic-e2e-fault-key");
    await page.getByRole("button", { name: "配置会话 Key" }).click();
    await page.getByRole("button", { name: "预览发送内容与费用" }).click();
    const dialog = page.getByRole("dialog"); await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "开始 AI 解析" }).click();
    await expect(page.getByText(/请求超时，可能已经收费/)).toBeVisible();
    await page.reload(); await page.getByLabel("选择已上传的简历").selectOption(resume.id);
    await expect(page.getByText(/请求超时，可能已经收费/)).toBeVisible();
    const run = (await (await page.request.get(`/api/v1/resumes/${resume.id}/extraction`)).json()).recent_runs[0];
    expect(run.call_count).toBe(1); expect(run.billing_status).toBe("unknown");
    expect((await (await page.request.get("/api/v1/profile")).json()).candidates).toEqual([]);
    await page.getByRole("button", { name: "撤销 Key" }).click();
    await expect(page.getByText("当前会话未配置 Key", { exact: true })).toBeVisible();
  } finally { await control("ai-mode", { mode: "default" }); }
});
