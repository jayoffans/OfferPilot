import { randomBytes } from "node:crypto";
import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

export const syntheticPassword = "Synthetic-OfferPilot-E2E-2026";

export async function control(path: string, body: unknown = {}) {
  const origin = process.env.OFFERPILOT_E2E_API_ORIGIN;
  const secret = process.env.OFFERPILOT_E2E_CONTROL_SECRET;
  if (!origin || !secret) throw new Error("Missing private isolated E2E control channel");
  const response = await fetch(`${origin}/__e2e/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-OfferPilot-E2E-Control": secret },
    body: JSON.stringify(body), signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Isolated fixture operation failed (${response.status})`);
  return response;
}

export async function invitation() {
  const email = `e2e-${randomBytes(8).toString("hex")}@example.com`;
  const response = await control("invite", { email });
  const { invite_token } = await response.json() as { invite_token: string };
  return { email, inviteToken: invite_token };
}

export async function createAccount(request: APIRequestContext) {
  const account = await invitation();
  const response = await request.post("/api/v1/auth/register", {
    data: { email: account.email, password: syntheticPassword, invite_token: account.inviteToken },
  });
  expect(response.status()).toBe(201);
  return account;
}

export async function loginUI(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("邮箱地址").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(syntheticPassword);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
}

export async function uploadUI(page: Page, pdf: Buffer, name = "synthetic-resume.pdf") {
  await page.goto("/resumes");
  await page.getByRole("button", { name: "上传简历", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles({ name, mimeType: "application/pdf", buffer: pdf });
  const uploaded = page.waitForResponse((response) => response.url().endsWith("/api/v1/resumes") && response.request().method() === "POST");
  await dialog.getByRole("button", { name: "上传简历", exact: true }).click();
  const response = await uploaded;
  expect(response.status()).toBe(201);
  const result = await response.json() as { id: string; file_name: string };
  await expect(dialog).toBeHidden();
  // Closing the dialog precedes the router transition. Wait for the actual
  // destination before later menu clicks can race with the old page unmount.
  await expect(page).toHaveURL(new RegExp(`/resumes/${result.id}$`));
  await expect(page.getByRole("heading", { name: result.file_name, exact: true })).toBeVisible();
  return result;
}

export async function csrf(request: APIRequestContext) {
  const response = await request.get("/api/v1/auth/csrf");
  expect(response.status()).toBe(200);
  return (await response.json() as { csrf_token: string }).csrf_token;
}

export async function logoutUI(page: Page) {
  // Account controls may be either in the desktop sidebar or mobile header.
  await page.getByRole("button", { name: /账户菜单/ }).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
}

export const test = base.extend<{ pdf: Buffer }>({
  pdf: async ({}, provide) => {
    await control("reset-throttle");
    const response = await control("pdf");
    await provide(Buffer.from(await response.arrayBuffer()));
  },
});

export { expect };
