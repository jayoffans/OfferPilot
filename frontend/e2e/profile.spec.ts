import { mkdir } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { Page, APIRequestContext } from "@playwright/test";
import { test, expect, control, createAccount, loginUI, logoutUI, uploadUI, csrf } from "./fixtures";

const endpoint = "/api/v1/profile";
const screenshots = path.resolve("artifacts/screenshots");

async function profile(request: APIRequestContext) {
  const response = await request.get(endpoint);
  expect(response.status()).toBe(200);
  return response.json();
}

async function saveUI(page: Page, status = 200) {
  const saved = page.waitForResponse(response => response.url().endsWith(endpoint) && response.request().method() === "PUT");
  await page.getByRole("button", { name: "保存资料", exact: true }).click();
  expect((await saved).status()).toBe(status);
  if (status === 200) await expect(page.locator(".profile-savebar")).toContainText("当前资料已与服务器同步");
}

async function cleanScreenshot(page: Page, name: string, width: number) {
  await page.setViewportSize({ width, height: 1000 });
  await expect(page.getByRole("heading", { name: "我的资料", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "添加教育经历", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({ path: path.join(screenshots, `${name}-${width}.png`), fullPage: true });
  if (name === "profile-filled" && [1440, 390].includes(width)) {
    await page.screenshot({ path: path.join(screenshots, `${name}-${width}-viewport.png`) });
  }
}

test("画像真实数据库闭环：部分保存、四组编辑、刷新及退出重登录", async ({ page }) => {
  await mkdir(screenshots, { recursive: true });
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.getByRole("link", { name: "我的资料", exact: true }).first().click();
  const empty = await profile(page.request);
  expect(empty).toMatchObject({ revision: 0, education: [], skills: [], projects: [], experiences: [], candidates: [] });
  for (const width of [1440, 768, 390]) await cleanScreenshot(page, "profile-empty", width);
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("合成大学");
  await saveUI(page);
  let persisted = await profile(page.request);
  expect(persisted.education).toHaveLength(1);
  expect(persisted.education[0]).toMatchObject({ school: "合成大学", degree: null, major: null, end_date: null, end_date_kind: null, source: { kind: "manual" } });
  await page.reload();
  await expect(page.getByLabel("学校", { exact: true })).toHaveValue("合成大学");

  await page.getByLabel("学历", { exact: true }).fill("本科");
  await page.getByLabel("专业", { exact: true }).fill("计算机科学");
  await page.getByLabel("毕业时间", { exact: true }).fill("2027-06");
  await page.getByLabel("毕业时间类型", { exact: true }).selectOption("expected");
  await page.getByRole("button", { name: "添加技能", exact: true }).click();
  await page.getByLabel("技能名称", { exact: true }).fill("TypeScript");
  await page.getByRole("button", { name: "添加项目经历", exact: true }).click();
  await page.getByLabel("项目名称", { exact: true }).fill("合成测试项目");
  await page.getByLabel("项目内容与成果", { exact: true }).fill("实现合成资料表单，未编造项目成果。");
  await page.getByRole("button", { name: "添加实习 / 工作经历", exact: true }).click();
  await page.getByLabel("单位名称", { exact: true }).fill("合成测试公司");
  await page.getByLabel("经历类型", { exact: true }).selectOption("internship");
  await saveUI(page);
  persisted = await profile(page.request);
  expect(persisted.completion.populated_groups).toHaveLength(4);
  expect(persisted.skills[0]).toMatchObject({ name: "TypeScript", level: null });
  expect(persisted.projects[0].name).toBe("合成测试项目");
  expect(persisted.experiences[0]).toMatchObject({ organization: "合成测试公司", experience_type: "internship", end_date: null, is_current: null });
  for (const width of [1440, 768, 390]) await cleanScreenshot(page, "profile-filled", width);
  await page.reload();
  await expect(page.getByLabel("专业", { exact: true })).toHaveValue("计算机科学");
  await logoutUI(page);
  await loginUI(page, account.email);
  await page.goto("/profile");
  await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("TypeScript");
  await page.getByLabel("学校", { exact: true }).fill("合成大学（修订）");
  await saveUI(page);
  expect((await profile(page.request)).education[0].school).toBe("合成大学（修订）");
  expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] });
});

test("真实候选审核：证据、否定保护、确认修订拒绝及删除来源", async ({ page, pdf }) => {
  await mkdir(screenshots, { recursive: true });
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const uploaded = await uploadUI(page, pdf, "synthetic-profile-source.pdf");
  await control(`resume/${uploaded.id}/candidates`);
  await page.goto("/profile");
  let state = await profile(page.request);
  expect(state.skills).toEqual([]);
  expect(state.candidates.every((candidate: { status: string }) => candidate.status === "pending")).toBe(true);
  expect(state.completion.pending_count).toBe(3);
  for (const width of [1440, 768, 390]) await cleanScreenshot(page, "profile-review", width);
  await page.setViewportSize({ width: 1440, height: 1000 });

  const python = state.candidates.find((candidate: { value: { name: string } }) => candidate.value.name === "Python");
  const java = state.candidates.find((candidate: { value: { name: string } }) => candidate.value.name === "Java");
  const go = state.candidates.find((candidate: { value: { name: string } }) => candidate.value.name === "Go");
  expect(python.evidence_status).toBe("supported");
  expect(java.evidence_status).not.toBe("supported");
  expect(go.evidence_status).toBe("unsupported");
  const forbidden = await page.request.post(`${endpoint}/candidates/${java.id}/review`, {
    headers: { "X-CSRF-Token": await csrf(page.request) }, data: { revision: state.revision, action: "confirm" },
  });
  expect(forbidden.status()).toBe(422);
  expect((await profile(page.request)).skills).toEqual([]);

  // Semantic candidate cards allow inspection without reaching into app state.
  const pythonCard = page.locator(`[data-candidate-id="${python.id}"]`);
  await pythonCard.getByRole("button", { name: "确认事实", exact: true }).click();
  await expect.poll(async () => (await profile(page.request)).skills.length).toBe(1);
  await page.reload();
  const javaCard = page.locator(`[data-candidate-id="${java.id}"]`);
  await expect(javaCard.getByRole("button", { name: "确认事实", exact: true })).toBeDisabled();
  await javaCard.getByRole("button", { name: "修改后确认", exact: true }).click();
  const edit = page.getByRole("dialog");
  await edit.getByLabel("技能名称", { exact: true }).fill("Java 基础语法（用户补充）");
  await edit.getByLabel("掌握情况", { exact: true }).fill("学习中");
  await edit.getByLabel("我已核实以上内容符合本人实际情况", { exact: true }).check();
  await edit.getByRole("button", { name: "保存修改并确认", exact: true }).click();
  await expect(edit).toBeHidden();
  const goCard = page.locator(`[data-candidate-id="${go.id}"]`);
  await goCard.getByRole("button", { name: "拒绝候选", exact: true }).click();
  await expect.poll(async () => (await profile(page.request)).completion.pending_count).toBe(0);
  state = await profile(page.request);
  expect(state.candidates.find((candidate: { id: string }) => candidate.id === python.id).status).toBe("confirmed");
  expect(state.candidates.find((candidate: { id: string }) => candidate.id === java.id).status).toBe("modified");
  expect(state.candidates.find((candidate: { id: string }) => candidate.id === go.id).status).toBe("rejected");
  expect(state.skills.find((entry: { name: string }) => entry.name === "Python").source.kind).toBe("resume_confirmed");
  expect(state.skills.find((entry: { name: string }) => entry.name.startsWith("Java")).source.kind).toBe("user_modified");

  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("独立手工合成大学");
  await saveUI(page);
  const before = await profile(page.request);
  await page.goto(`/resumes/${uploaded.id}`);
  await page.getByRole("button", { name: "删除简历", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "这份简历已删除", exact: true })).toBeVisible();
  const deleted = await profile(page.request);
  expect(deleted.revision).toBeGreaterThan(before.revision);
  expect(deleted.candidates).toEqual([]);
  expect(deleted.education[0].school).toBe("独立手工合成大学");
  expect(deleted.skills).toHaveLength(2);
  expect(deleted.skills.every((entry: { source: { resume_id: unknown; evidence: unknown[] } }) => entry.source.resume_id === null && entry.source.evidence.length === 0)).toBe(true);
  await page.goto("/profile");
  await expect(page.getByText("来源：来源简历已删除", { exact: true }).first()).toBeVisible();
  await page.getByRole("region", { name: "技术技能", exact: true }).locator("summary").first().click();
  await expect(page.getByText("来源简历已删除，已确认内容仍保留", { exact: true }).first()).toBeVisible();
  await cleanScreenshot(page, "profile-source-deleted", 1440);
});

test("画像授权：匿名、双用户、伪造归属、条目和候选 ID", async ({ page, browser, pdf }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const source = await uploadUI(page, pdf);
  await control(`resume/${source.id}/candidates`);
  const initial = await profile(page.request);
  const token = await csrf(page.request);
  expect((await page.request.put(endpoint, { data: { revision: initial.revision, education: [], skills: [], projects: [], experiences: [] } })).status()).toBe(403);
  const ownerSave = await page.request.put(endpoint, {
    headers: { "X-CSRF-Token": token }, data: { revision: initial.revision, education: [{ school: "合成私有学校" }], skills: [], projects: [], experiences: [] },
  });
  expect(ownerSave.status()).toBe(200);
  const owned = await ownerSave.json();
  const other = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  const anonymous = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  try {
    expect((await anonymous.request.get(endpoint)).status()).toBe(401);
    const accountB = await createAccount(other.request);
    const otherPage = await other.newPage();
    await loginUI(otherPage, accountB.email);
    const empty = await profile(other.request);
    expect(empty.education).toEqual([]);
    expect(empty.candidates).toEqual([]);
    const headers = { "X-CSRF-Token": await csrf(other.request) };
    const body = { revision: 0, education: [], skills: [], projects: [], experiences: [] };
    expect((await other.request.put(endpoint, { headers, data: { ...body, user_id: owned.user_id ?? randomUUID() } })).status()).toBe(422);
    expect((await other.request.put(endpoint, { headers, data: { ...body, education: [{ id: owned.education[0].id, school: "伪造学校" }] } })).status()).toBe(404);
    const foreign = await other.request.post(`${endpoint}/candidates/${initial.candidates[0].id}/review`, { headers, data: { revision: 0, action: "confirm" } });
    const missing = await other.request.post(`${endpoint}/candidates/${randomUUID()}/review`, { headers, data: { revision: 0, action: "confirm" } });
    expect(foreign.status()).toBe(404);
    expect(await foreign.json()).toEqual(await missing.json());
    expect((await other.request.get(`${endpoint}?user_id=${account.email}`)).status()).toBe(404);
    expect((await profile(page.request)).education[0].school).toBe("合成私有学校");
  } finally { await other.close(); await anonymous.close(); }
});

test("两个真实页面并发编辑：409 保留输入，未保存导航和退出提醒", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.goto("/profile");
  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("合成学校基线");
  await saveUI(page);
  const second = await page.context().newPage();
  try {
    await second.goto("/profile");
    await expect(second.getByLabel("学校", { exact: true })).toHaveValue("合成学校基线");
    await page.getByLabel("学校", { exact: true }).fill("第一页已保存");
    await second.getByLabel("学校", { exact: true }).fill("第二页未保存");
    await saveUI(page);
    await saveUI(second, 409);
    await expect(second.getByLabel("学校", { exact: true })).toHaveValue("第二页未保存");
    expect((await profile(page.request)).education[0].school).toBe("第一页已保存");
    await expect(second.getByText(/版本|更新|冲突/).first()).toBeVisible();
    await mkdir(screenshots, { recursive: true });
    await cleanScreenshot(second, "profile-conflict", 1440);
    await second.getByRole("link", { name: "我的简历", exact: true }).first().click();
    const guard = second.getByRole("alertdialog");
    await expect(guard).toBeVisible();
    await guard.getByRole("button", { name: "继续编辑", exact: true }).click();
    await expect(second).toHaveURL(/\/profile$/);
    await expect(second.getByLabel("学校", { exact: true })).toHaveValue("第二页未保存");
    await second.getByRole("button", { name: /账户菜单/ }).click();
    await second.getByRole("menuitem", { name: "退出登录" }).click();
    await expect(second.getByRole("alertdialog")).toBeVisible();
    await second.getByRole("alertdialog").getByRole("button", { name: "继续编辑", exact: true }).click();
    expect((await second.request.get("/api/v1/me")).status()).toBe(200);
  } finally { await second.close(); }
});

test("浏览器返回后恢复会话草稿；远程新版仍冲突，退出清除草稿", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.goto("/resumes");
  await page.getByRole("link", { name: "我的资料", exact: true }).first().click();
  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("会话内未保存的合成学校");
  await page.goBack();
  await expect(page).toHaveURL(/\/resumes$/);
  expect((await profile(page.request)).education).toEqual([]);
  await page.getByRole("link", { name: "我的资料", exact: true }).first().click();
  await expect(page.getByLabel("学校", { exact: true })).toHaveValue("会话内未保存的合成学校");
  await expect(page.getByText(/恢复了本次会话尚未保存的编辑/)).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/resumes$/);
  const remote = await page.request.put(endpoint, {
    headers: { "X-CSRF-Token": await csrf(page.request) },
    data: { revision: 0, education: [{ school: "服务器已有新版合成学校" }], skills: [], projects: [], experiences: [] },
  });
  expect(remote.status()).toBe(200);
  await page.getByRole("link", { name: "我的资料", exact: true }).first().click();
  await expect(page.getByLabel("学校", { exact: true })).toHaveValue("会话内未保存的合成学校");
  await expect(page.getByRole("button", { name: "保存资料", exact: true })).toBeDisabled();
  await expect(page.locator(".profile-latest")).toContainText("服务器已有新版合成学校");
  await page.getByRole("button", { name: "放弃本次修改，重新载入", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "放弃修改并重新载入", exact: true }).click();
  await expect(page.getByLabel("学校", { exact: true })).toHaveValue("服务器已有新版合成学校");
  await page.getByLabel("学校", { exact: true }).fill("退出前尚未保存的合成学校");
  await page.getByRole("button", { name: /账户菜单/ }).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "放弃修改并退出", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await loginUI(page, account.email);
  await page.goto("/profile");
  await expect(page.getByLabel("学校", { exact: true })).toHaveValue("服务器已有新版合成学校");
  await expect(page.getByText(/恢复了本次会话尚未保存的编辑/)).toHaveCount(0);
  expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] });
});
