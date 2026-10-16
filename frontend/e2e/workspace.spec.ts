import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { test, expect, control, invitation, createAccount, loginUI, logoutUI, uploadUI, csrf, syntheticPassword } from "./fixtures";

test("真实闭环：邀请注册、刷新重登录、改名、原件下载、实际删除与退出", async ({ page, pdf }) => {
  const screenshots = path.resolve("artifacts/screenshots");
  await mkdir(screenshots, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const account = await invitation();
  await page.goto("/register");
  await page.getByLabel("邮箱地址").fill(account.email);
  await page.getByLabel("密码", { exact: true }).fill(syntheticPassword);
  await page.getByLabel("邀请码").fill(account.inviteToken);
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await loginUI(page, account.email);
  expect((await page.request.get("/api/v1/resumes")).status()).toBe(200);
  const empty = await page.request.get("/api/v1/resumes");
  expect(await empty.json()).toEqual([]);

  const uploaded = await uploadUI(page, pdf);
  await page.goto(`/resumes/${uploaded.id}`);
  await expect(page.getByRole("heading", { name: uploaded.file_name, exact: true })).toBeVisible();
  await expect(page.getByText("文本解析完成", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("尚未启用，可手动确认资料", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: uploaded.file_name, exact: true })).toBeVisible();
  await logoutUI(page);
  await loginUI(page, account.email);
  await page.goto(`/resumes/${uploaded.id}`);
  await page.getByRole("button", { name: "修改文件名", exact: true }).click();
  const rename = page.getByRole("dialog");
  await rename.getByLabel("文件名", { exact: true }).fill("秋招简历-v2.pdf");
  await page.screenshot({ path: path.join(screenshots, "rename-dialog-1440.png"), fullPage: true });
  await rename.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(rename).toBeHidden();
  await expect(page.getByRole("heading", { name: "秋招简历-v2.pdf", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "秋招简历-v2.pdf", exact: true })).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载原件", exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("秋招简历-v2.pdf");
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  expect(await readFile(downloadedPath!)).toEqual(pdf);
  await control(`resume/${uploaded.id}/derived`);
  const before = await (await control(`resume/${uploaded.id}/inspect`)).json();
  expect(before).toMatchObject({ document_exists: true, text_exists: true, object_exists: true, claim_exists: true, profile_count: 1, fact_count: 1 });
  await page.getByRole("button", { name: "删除简历", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, "delete-confirm-1440.png"), fullPage: true });
  await dialog.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "这份简历已删除", exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, "deleted-1440.png"), fullPage: true });
  const after = await (await control(`resume/${uploaded.id}/inspect`)).json();
  expect(after).toMatchObject({ document_exists: false, text_exists: false, object_exists: false, claim_exists: false, partial_exists: false, profile_count: 0, fact_count: 0, operation_status: "deleted", operation_has_reference: false });
  expect((await page.request.get(`/api/v1/resumes/${uploaded.id}/download`)).status()).toBe(404);
  expect((await page.request.delete(`/api/v1/resumes/${uploaded.id}`, { headers: { "X-CSRF-Token": await csrf(page.request) } })).status()).toBe(204);
  await logoutUI(page);
  expect((await page.request.get("/api/v1/me")).status()).toBe(401);
});

test("双账户、匿名、CSRF 和未知资源隔离由真实 API 执行", async ({ page, browser, pdf }) => {
  const owner = await createAccount(page.request);
  await loginUI(page, owner.email);
  const uploaded = await uploadUI(page, pdf, "owner-private.pdf");
  const token = await csrf(page.request);
  const endpoint = `/api/v1/resumes/${uploaded.id}`;
  expect((await page.request.patch(endpoint, { data: { file_name: "forbidden.pdf" } })).status()).toBe(403);
  expect((await page.request.delete(endpoint)).status()).toBe(403);
  expect((await page.request.patch(endpoint, { data: { file_name: "forbidden.pdf" }, headers: { "X-CSRF-Token": token, Origin: "https://untrusted.example.com" } })).status()).toBe(403);
  const otherContext = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  const anonymousContext = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  try {
    const other = await createAccount(otherContext.request);
    const otherPage = await otherContext.newPage();
    await loginUI(otherPage, other.email);
    expect(await (await otherContext.request.get("/api/v1/resumes")).json()).toEqual([]);
    const otherToken = await csrf(otherContext.request);
    for (const suffix of ["", "/download", "/lifecycle"]) {
      expect((await otherContext.request.get(`${endpoint}${suffix}`)).status()).toBe(404);
      expect((await anonymousContext.request.get(`${endpoint}${suffix}`)).status()).toBe(401);
    }
    expect((await otherContext.request.delete(endpoint, { headers: { "X-CSRF-Token": otherToken } })).status()).toBe(404);
    expect((await otherContext.request.patch(endpoint, { headers: { "X-CSRF-Token": otherToken }, data: { file_name: "stolen.pdf" } })).status()).toBe(404);
    const missing = await otherContext.request.get(`/api/v1/resumes/${randomUUID()}`);
    const foreign = await otherContext.request.get(endpoint);
    expect(await foreign.json()).toEqual(await missing.json());
    await otherPage.goto(`/resumes/${uploaded.id}`);
    await expect(otherPage.locator(".notice[role=alert]")).toContainText(/不存在|无法访问|未找到/);
    await expect(otherPage.getByText("owner-private.pdf", { exact: true })).toHaveCount(0);
    await anonymousContext.newPage().then(async (anonymous) => {
      await anonymous.goto("/resumes");
      await expect(anonymous).toHaveURL(/\/login(?:\?|$)/);
    });
    expect((await page.request.get(`${endpoint}/download`)).status()).toBe(200);
    const cookie = (await page.context().cookies()).find((entry) => entry.name === "offerpilot_session");
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.sameSite).toBe("Lax");
    expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] });
  } finally {
    await otherContext.close();
    await anonymousContext.close();
  }
});

test("真实文件故障：删除 503 保留失败状态，刷新后重试完成实际清理", async ({ page, pdf }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const uploaded = await uploadUI(page, pdf, "recoverable-delete.pdf");
  await page.goto(`/resumes/${uploaded.id}`);
  await control(`resume/${uploaded.id}/fault`, { mode: "delete" });
  try {
    await page.getByRole("button", { name: "删除简历", exact: true }).click();
    const deletion = page.waitForResponse((response) => response.request().method() === "DELETE");
    await page.getByRole("alertdialog").getByRole("button", { name: "确认删除", exact: true }).click();
    expect((await deletion).status()).toBe(503);
    await expect(page.locator(".notice[role=alert]").first()).toContainText(/删除|清理/);
    const failed = await (await control(`resume/${uploaded.id}/inspect`)).json();
    expect(failed).toMatchObject({ document_exists: true, text_exists: false, object_exists: true, operation_status: "delete_failed", lifecycle_status: "delete_failed" });
    await page.reload();
    await expect(page.getByText("删除失败", { exact: true }).first()).toBeVisible();
    await control(`resume/${uploaded.id}/fault`, { mode: "restore" });
    await page.getByRole("button", { name: "重试删除", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "确认删除", exact: true }).click();
    await expect(page.getByRole("heading", { name: "这份简历已删除", exact: true })).toBeVisible();
    const cleaned = await (await control(`resume/${uploaded.id}/inspect`)).json();
    expect(cleaned).toMatchObject({ document_exists: false, object_exists: false, claim_exists: false, operation_status: "deleted" });
  } finally {
    await control(`resume/${uploaded.id}/fault`, { mode: "restore" });
  }
});

test("真实 409 原件校验失败和无效 PDF 上传不会显示成功", async ({ page, pdf }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const uploaded = await uploadUI(page, pdf, "integrity-check.pdf");
  await page.goto(`/resumes/${uploaded.id}`);
  await control(`resume/${uploaded.id}/fault`, { mode: "integrity" });
  try {
    const download = page.waitForResponse((response) => response.url().endsWith("/download"));
    await page.getByRole("button", { name: "下载原件", exact: true }).click();
    expect((await download).status()).toBe(409);
    await expect(page.locator(".notice[role=alert]").first()).toContainText(/校验|冲突/);
  } finally {
    await control(`resume/${uploaded.id}/fault`, { mode: "restore" });
  }
  await page.goto("/resumes");
  await page.getByRole("button", { name: "上传简历", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles({ name: "invalid.pdf", mimeType: "application/pdf", buffer: Buffer.from("Not a PDF") });
  await dialog.getByRole("button", { name: "上传简历", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText(/PDF|文件|格式/);
  expect((await (await page.request.get("/api/v1/resumes")).json() as unknown[]).length).toBe(1);
});

test("真实登录 429 显示限流且不自动重试", async ({ page }) => {
  await control("reset-throttle");
  const email = "e2e-rate-limit@example.com";
  for (let attempt = 0; attempt < 5; attempt++) {
    const response = await page.request.post("/api/v1/auth/login", { data: { email, password: syntheticPassword } });
    expect(response.status()).toBe(401);
  }
  await page.goto("/login");
  await page.getByLabel("邮箱地址").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(syntheticPassword);
  let requests = 0;
  page.on("request", (request) => { if (request.url().endsWith("/api/v1/auth/login")) requests++; });
  const response = page.waitForResponse((entry) => entry.url().endsWith("/api/v1/auth/login"));
  await page.getByRole("button", { name: "登录", exact: true }).click();
  expect((await response).status()).toBe(429);
  await expect(page.locator(".notice[role=alert]")).toContainText(/频繁|稍后|等待/);
  await page.waitForTimeout(1000);
  expect(requests).toBe(1);
});

test("真实服务关闭后错误可恢复；服务端会话过期返回登录", async ({ page, pdf }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await uploadUI(page, pdf, "outage-resume.pdf");
  await control("backend-outage");
  await page.waitForTimeout(1200);
  await page.reload();
  await expect(page.locator(".notice[role=alert]").first()).toContainText(/服务|网络|连接|稍后/);
  await expect.poll(async () => {
    try { return (await fetch(`${process.env.OFFERPILOT_E2E_API_ORIGIN}/health`, { signal: AbortSignal.timeout(1000) })).status; }
    catch { return 0; }
  }, { timeout: 15_000, intervals: [250, 500] }).toBe(200);
  await page.reload();
  await expect(page.getByText("outage-resume.pdf", { exact: true }).first()).toBeVisible();
  await control("expire-session", { email: account.email });
  await page.reload();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByRole("status")).toContainText(/会话|登录|失效/);
  expect((await page.request.get("/api/v1/me")).status()).toBe(401);
});

test("多个真实文件、安全文件名与 1440/768/390 响应式截图", async ({ page, pdf }) => {
  const account = await createAccount(page.request);
  const screenshots = path.resolve("artifacts/screenshots");
  await mkdir(screenshots, { recursive: true });
  // These shots intentionally precede filling any authentication secrets.
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "登录", exact: true })).toBeEnabled();
    await page.screenshot({ path: path.join(screenshots, `login-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.goto("/register");
    await expect(page.getByRole("button", { name: "创建账户", exact: true })).toBeEnabled();
    await page.screenshot({ path: path.join(screenshots, `register-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await loginUI(page, account.email);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "上传第一份简历", exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, `dashboard-empty-${width}.png`), fullPage: true });
    await page.goto("/resumes");
    await expect(page.getByRole("heading", { name: "准备好你的第一份简历", exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, `resumes-empty-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "上传简历", exact: true }).click();
    const uploadDialog = page.getByRole("dialog");
    await expect(uploadDialog).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, `upload-dialog-${width}.png`), fullPage: true });
    await page.keyboard.press("Escape");
    await expect(uploadDialog).toBeHidden();
    await expect(page.getByRole("button", { name: "上传简历", exact: true })).toBeFocused();
  }
  const csrfToken = await csrf(page.request);
  for (let index = 1; index <= 12; index++) {
    const response = await page.request.post("/api/v1/resumes", {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: { file: { name: `合成简历-${String(index).padStart(2, "0")}.pdf`, mimeType: "application/pdf", buffer: pdf } },
    });
    expect(response.status()).toBe(201);
  }
  const list = await (await page.request.get("/api/v1/resumes")).json() as { id: string }[];
  const injectionName = "<img src=x onerror=alert(1)>.pdf";
  const rename = await page.request.patch(`/api/v1/resumes/${list[0].id}`, { headers: { "X-CSRF-Token": csrfToken }, data: { file_name: injectionName } });
  expect(rename.status()).toBe(200);
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "完善我的资料", exact: true })).toBeVisible();
    const workbench = await page.request.get("/api/v1/dashboard");
    expect(workbench.status()).toBe(200);
    expect((await workbench.json()).onboarding.resume_count).toBe(12);
    await page.screenshot({ path: path.join(screenshots, `dashboard-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.goto("/resumes");
    await expect(page.getByText(injectionName, { exact: true }).first()).toBeVisible();
    expect(await page.locator("img[onerror]").count()).toBe(0);
    await page.screenshot({ path: path.join(screenshots, `resumes-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.goto(`/resumes/${list[0].id}`);
    await expect(page.getByRole("heading", { name: injectionName, exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, `resume-detail-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("button", { name: "修改文件名", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("文件名", { exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "修改文件名", exact: true })).toBeFocused();
  }
  await page.goto("/resumes");
  await expect(page.locator(".resume-row")).toHaveCount(8);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.locator(".resume-row")).toHaveCount(4);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索简历文件名" }).fill("合成简历-01");
  await expect(page.locator(".resume-row")).toHaveCount(1);
  await page.getByRole("button", { name: "清除搜索", exact: true }).click();
  await page.getByRole("combobox", { name: "简历排序" }).selectOption("name");
  const names = await page.locator(".resume-row strong").allTextContents();
  expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b, "zh-CN")));
});
