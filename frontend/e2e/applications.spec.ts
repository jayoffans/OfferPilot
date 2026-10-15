import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { APIRequestContext, Page } from "@playwright/test";
import { test, expect, control, createAccount, loginUI, logoutUI, csrf } from "./fixtures";

const endpoint = "/api/v1/applications";
const output = path.resolve("artifacts/s4-1");
test.beforeEach(async () => { await control("reset-throttle"); });

async function job(request: APIRequestContext, title = "后端开发实习生（合成）") {
  const response = await request.post("/api/v1/jobs", {
    headers: { "X-CSRF-Token": await csrf(request) },
    data: { idempotency_key: randomUUID(), title, company: "合成科技", location: "上海", jd_text: "必须熟悉 Python。" },
  });
  expect(response.status()).toBe(201);
  return response.json();
}
async function read(request: APIRequestContext, id: string) {
  const response = await request.get(`${endpoint}/${id}`);
  expect(response.status()).toBe(200);
  return response.json();
}
async function events(request: APIRequestContext, id: string) {
  const response = await request.get(`${endpoint}/${id}/events`);
  expect(response.status()).toBe(200);
  return response.json();
}
async function start(page: Page, saved: { id: string }) {
  await page.goto(`/jobs/${saved.id}`);
  const response = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === "POST");
  await page.getByRole("button", { name: "准备投递", exact: true }).click();
  expect((await response).status()).toBe(201);
  const application = await (await response).json();
  await expect(page).toHaveURL(new RegExp(`/applications/${application.id}$`));
  await expect(page.getByLabel("申请状态", { exact: true })).toHaveValue("preparing");
  return application;
}
async function save(page: Page, id: string, status = 200) {
  const response = page.waitForResponse(r => r.url().endsWith(`${endpoint}/${id}`) && r.request().method() === "PUT");
  await page.getByRole("button", { name: "保存申请修改", exact: true }).click();
  expect((await response).status()).toBe(status);
}
async function screenshot(page: Page, name: string, width: number) {
  await mkdir(output, { recursive: true });
  await page.setViewportSize({ width, height: 1000 });
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
  if (width === 1440 || width === 390) await page.screenshot({ path: path.join(output, `${name}-${width}-viewport.png`) });
}

test("中文申请闭环：收藏独立、创建、投递时间、备注、日期行动、时间线及重登录恢复", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.goto("/applications");
  await expect(page.getByRole("heading", { name: "从一个认真考虑的机会开始" })).toBeVisible();
  await screenshot(page, "applications-empty", 390);
  const saved = await job(page.request);
  const favorite = await page.request.patch(`/api/v1/jobs/${saved.id}/favorite`, {
    headers: { "X-CSRF-Token": await csrf(page.request) }, data: { revision: saved.revision, is_favorite: true },
  });
  expect(favorite.status()).toBe(200);
  expect((await (await page.request.get(endpoint)).json()).total).toBe(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const application = await start(page, saved);
  await page.getByLabel("申请状态", { exact: true }).selectOption("submitted");
  await save(page, application.id);
  expect((await read(page.request, application.id)).submitted_at).toBeNull();
  await page.getByLabel("实际投递时间（北京时间）", { exact: true }).fill("2026-10-14T00:15");
  await page.getByLabel("申请备注", { exact: true }).fill("已通过校招官网手动投递，等待招聘方回复。");
  await page.getByLabel("下一步行动", { exact: true }).fill("准备技术面试");
  await page.getByLabel("行动截止日期", { exact: true }).fill("2099-10-16");
  await page.getByLabel("行动备注", { exact: true }).fill("复习项目接口与数据库设计");
  await save(page, application.id);
  let state = await read(page.request, application.id);
  expect(new Date(state.submitted_at).toISOString()).toBe("2026-10-13T16:15:00.000Z");
  expect(state.next_action.due_time).toBeNull();
  expect(state.next_action.due_date).toBe("2099-10-16");
  expect(state.next_action.timezone).toBe("Asia/Shanghai");
  let history = await events(page.request, application.id);
  expect(history.total).toBe(5);
  expect(history.items.map((item: { type: string }) => item.type)).toEqual(expect.arrayContaining(["application_created", "status_changed", "notes_changed", "next_action_changed", "submitted_at_changed"]));
  await expect(page.getByRole("heading", { name: "申请时间线", exact: true })).toBeVisible();
  for (const width of [1440, 768, 390]) await screenshot(page, "application-detail", width);
  await page.reload();
  await expect(page.getByLabel("申请备注", { exact: true })).toHaveValue(state.notes);
  await expect(page.getByLabel("下一步行动", { exact: true })).toHaveValue("准备技术面试");
  await logoutUI(page);
  await loginUI(page, account.email);
  await page.goto(`/applications/${application.id}`);
  await expect(page.getByLabel("申请状态", { exact: true })).toHaveValue("submitted");
  await page.getByLabel("申请状态", { exact: true }).selectOption("interview");
  await save(page, application.id);
  expect((await read(page.request, application.id)).status).toBe("interview");
  // A correction can skip backwards; the original event remains in history.
  await page.getByLabel("申请状态", { exact: true }).selectOption("assessment");
  await save(page, application.id);
  state = await read(page.request, application.id);
  expect(state.status).toBe("assessment");
  history = await events(page.request, application.id);
  expect(history.total).toBe(7);
  await page.goto("/applications");
  await page.getByLabel("搜索申请", { exact: true }).fill("合成科技");
  await expect(page.getByRole("link", { name: /后端开发实习生（合成）/ })).toBeVisible();
  for (const width of [1440, 768, 390]) await screenshot(page, "applications-list", width);
  await page.getByLabel("申请状态筛选").selectOption("offer");
  await expect(page.getByRole("heading", { name: "没有找到符合筛选的申请" })).toBeVisible();
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, "synthetic-application.json"), JSON.stringify({ application: state, events: history }, null, 2));
  expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] });
});

test("申请真实双页面409与提交后丢失响应的原请求重试", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const application = await start(page, await job(page.request));
  const other = await page.context().newPage();
  try {
    await other.goto(`/applications/${application.id}`);
    await expect(other.getByLabel("申请备注", { exact: true })).toBeVisible();
    await page.getByLabel("申请备注", { exact: true }).fill("第一页已保存");
    await other.getByLabel("申请备注", { exact: true }).fill("第二页的输入需保留");
    await save(page, application.id);
    await save(other, application.id, 409);
    await expect(other.getByLabel("申请备注", { exact: true })).toHaveValue("第二页的输入需保留");
    expect((await read(page.request, application.id)).notes).toBe("第一页已保存");
    await screenshot(other, "application-conflict", 1440);
  } finally { await other.close(); }
  await page.getByLabel("申请备注", { exact: true }).fill("服务端已提交但响应丢失");
  let failedOnce = false;
  await page.route(`**${endpoint}/${application.id}`, async route => {
    if (route.request().method() === "PUT" && !failedOnce) {
      failedOnce = true;
      const committed = await route.fetch();
      expect(committed.status()).toBe(200);
      await route.abort("failed");
    } else await route.continue();
  });
  const before = (await events(page.request, application.id)).total;
  await page.getByRole("button", { name: "保存申请修改", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  const committed = await read(page.request, application.id);
  expect(committed.notes).toBe("服务端已提交但响应丢失");
  await save(page, application.id);
  expect((await read(page.request, application.id)).revision).toBe(committed.revision);
  expect((await events(page.request, application.id)).total).toBe(before + 1);
});

test("行动完成取消、恶意备注、申请授权与安全关联删除", async ({ page, browser }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const saved = await job(page.request);
  const application = await start(page, saved);
  const note = '<script>window.__applicationInjected=1</script><img src=x onerror="window.__applicationInjected=2">';
  await page.getByLabel("申请备注", { exact: true }).fill(note);
  await page.getByLabel("下一步行动", { exact: true }).fill("跟进 HR（手工计划）");
  await page.getByLabel("行动截止日期", { exact: true }).fill("2020-01-01");
  await page.getByLabel("具体时间（可选）", { exact: true }).fill("09:30");
  await save(page, application.id);
  expect((await read(page.request, application.id)).is_overdue).toBe(true);
  await screenshot(page, "application-overdue", 390);
  expect(await page.evaluate(() => "__applicationInjected" in window)).toBe(false);
  const completed = page.waitForResponse(r => r.url().endsWith(`${endpoint}/${application.id}`) && r.request().method() === "PUT");
  await page.getByRole("button", { name: "完成行动", exact: true }).click();
  expect((await completed).status()).toBe(200);
  expect((await read(page.request, application.id)).next_action.status).toBe("completed");
  expect((await read(page.request, application.id)).is_overdue).toBe(false);
  // Reopening is explicit; editing does not silently reset a completed action.
  const reopened = page.waitForResponse(r => r.url().endsWith(`${endpoint}/${application.id}`) && r.request().method() === "PUT");
  await page.getByRole("button", { name: "恢复待完成", exact: true }).click();
  expect((await reopened).status()).toBe(200);
  await page.getByLabel("下一步行动", { exact: true }).fill("等待约定时间后询问进度");
  await save(page, application.id);
  await page.getByRole("button", { name: "取消行动", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: /确认取消/ }).click();
  await expect.poll(async () => (await read(page.request, application.id)).next_action.status).toBe("cancelled");
  const foreign = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  const anonymous = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  try {
    const secondAccount = await createAccount(foreign.request);
    const secondPage = await foreign.newPage();
    await loginUI(secondPage, secondAccount.email);
    const foreignHeaders = { "X-CSRF-Token": await csrf(foreign.request) };
    const url = `${endpoint}/${application.id}`;
    expect((await anonymous.request.get(url)).status()).toBe(401);
    expect((await foreign.request.get(endpoint)).status()).toBe(200);
    expect((await (await foreign.request.get(endpoint)).json()).total).toBe(0);
    for (const path of [url, `${url}/events`]) expect((await foreign.request.get(path)).status()).toBe(404);
    const state = await read(page.request, application.id);
    expect((await foreign.request.put(url, { headers: foreignHeaders, data: { revision: state.revision, status: "offer", notes: "forged", submitted_at: null, next_action: null, idempotency_key: randomUUID() } })).status()).toBe(404);
    expect((await foreign.request.delete(url, { headers: foreignHeaders, data: { revision: state.revision, idempotency_key: randomUUID(), confirmed: true } })).status()).toBe(404);
    const headers = { "X-CSRF-Token": await csrf(page.request) };
    expect((await page.request.put(url, { data: { revision: state.revision, status: "offer", notes: "", submitted_at: null, next_action: null, idempotency_key: randomUUID() } })).status()).toBe(403);
    expect((await page.request.delete(`/api/v1/jobs/${saved.id}`, { headers, data: { revision: saved.revision } })).status()).toBe(409);
    expect((await read(page.request, application.id)).notes).toBe(note);
    const deletion = { revision: state.revision, idempotency_key: randomUUID(), confirmed: true };
    expect((await page.request.delete(url, { headers, data: deletion })).status()).toBe(204);
    expect((await page.request.delete(url, { headers, data: deletion })).status()).toBe(204);
    expect((await page.request.get(`${url}/events`)).status()).toBe(404);
    expect((await page.request.delete(`/api/v1/jobs/${saved.id}`, { headers, data: { revision: saved.revision } })).status()).toBe(204);
  } finally { await foreign.close(); await anonymous.close(); }
});

test("匹配历史失效仍可跟踪、明确再次投递与界面确认删除", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const headers = { "X-CSRF-Token": await csrf(page.request) };
  const initial = await (await page.request.get("/api/v1/profile")).json();
  const profileResponse = await page.request.put("/api/v1/profile", { headers, data: {
    revision: initial.revision, education: [], skills: [{ name: "Python", level: "熟悉" }], projects: [], experiences: [],
  } });
  expect(profileResponse.status()).toBe(200);
  const profile = await profileResponse.json();
  const savedResponse = await page.request.post("/api/v1/jobs", { headers, data: {
    idempotency_key: randomUUID(), title: "校招开发工程师（历史合成）", company: "合成科技", jd_text: "必须熟悉 Python。",
    requirements: [{ type: "technical_skill", content: "必须熟悉 Python", priority: "must", review_status: "confirmed", evidence_text: "必须熟悉 Python。" }],
  } });
  expect(savedResponse.status()).toBe(201);
  const saved = await savedResponse.json();
  const matchResponse = await page.request.post(`/api/v1/jobs/${saved.id}/matches`, { headers, data: {
    idempotency_key: randomUUID(), profile_revision: profile.revision, job_revision: saved.revision,
  } });
  expect(matchResponse.status()).toBe(200);
  const match = await matchResponse.json();
  await page.goto(`/jobs/${saved.id}`);
  await page.getByRole("checkbox", { name: /关联这份匹配分析/ }).check();
  const createdResponse = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === "POST");
  await page.getByRole("button", { name: "准备投递", exact: true }).click();
  const first = await (await createdResponse).json();
  await expect(page).toHaveURL(new RegExp(`/applications/${first.id}$`));
  expect(first.match.id).toBe(match.id);
  const changed = await page.request.put("/api/v1/profile", { headers, data: {
    revision: profile.revision, education: [], skills: [{ name: "Go", level: "熟悉" }], projects: [], experiences: [],
  } });
  expect(changed.status()).toBe(200);
  await page.reload();
  await expect(page.getByText("关联的匹配分析已失效，仅作历史参考", { exact: true })).toBeVisible();
  expect((await read(page.request, first.id)).status).toBe("preparing");
  await screenshot(page, "application-match-stale", 1440);
  await page.getByLabel("申请状态", { exact: true }).selectOption("closed");
  await page.getByRole("button", { name: "保存申请修改", exact: true }).click();
  const closed = page.waitForResponse(r => r.url().endsWith(`${endpoint}/${first.id}`) && r.request().method() === "PUT");
  await page.getByRole("alertdialog").getByRole("button", { name: "确认保存状态", exact: true }).click();
  expect((await closed).status()).toBe(200);
  await page.goto(`/jobs/${saved.id}`);
  await page.getByRole("button", { name: "创建新一轮申请", exact: true }).click();
  const secondResponse = page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === "POST");
  await page.getByRole("dialog").getByRole("button", { name: "确认创建新一轮", exact: true }).click();
  expect((await secondResponse).status()).toBe(201);
  const second = await (await secondResponse).json();
  expect(second.attempt_number).toBe(2);
  expect((await read(page.request, first.id)).status).toBe("closed");
  for (const record of [second, first]) {
    await page.goto(`/applications/${record.id}`);
    await page.getByRole("button", { name: "删除申请记录", exact: true }).click();
    const removed = page.waitForResponse(r => r.url().endsWith(`${endpoint}/${record.id}`) && r.request().method() === "DELETE");
    await page.getByRole("alertdialog").getByRole("button", { name: "确认删除申请", exact: true }).click();
    expect((await removed).status()).toBe(204);
    await expect(page).toHaveURL(/\/applications$/);
  }
  await page.goto(`/jobs/${saved.id}`);
  await page.getByRole("button", { name: "删除职位", exact: true }).click();
  const removedJob = page.waitForResponse(r => r.url().includes(`/jobs/${saved.id}`) && r.request().method() === "DELETE");
  await page.getByRole("alertdialog").getByRole("button", { name: "确认删除职位", exact: true }).click();
  expect((await removedJob).status()).toBe(204);
});
