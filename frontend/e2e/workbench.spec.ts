import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { APIRequestContext, Locator, Page } from "@playwright/test";
import { test, expect, control, createAccount, invitation, loginUI, logoutUI, uploadUI, csrf, syntheticPassword } from "./fixtures";

const output = path.resolve("artifacts/s4-2");
const applications = "/api/v1/applications";
const dashboard = "/api/v1/dashboard";
const syntheticJD = "后端开发实习生（合成）\n任职要求：\n本科及以上学历。\n必须熟悉 Python。\n熟悉 Java 优先。";

test.beforeEach(async () => { await control("reset-throttle"); });

async function read(request: APIRequestContext, url: string) {
  const response = await request.get(url);
  expect(response.status()).toBe(200);
  return response.json();
}

async function screenshot(page: Page, name: string, widths = [1440, 768, 390], focus?: Locator) {
  await mkdir(output, { recursive: true });
  for (const width of widths) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo(0, 0);
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
    if (focus) await focus.evaluate(element => element.scrollIntoView({ block: "start" }));
    if ([1440, 390].includes(width)) await page.screenshot({ path: path.join(output, `${name}-${width}-viewport.png`) });
  }
}

async function saveApplication(page: Page, id: string) {
  const saved = page.waitForResponse(response => response.url().endsWith(`${applications}/${id}`) && response.request().method() === "PUT");
  await page.getByRole("button", { name: "保存申请修改", exact: true }).click();
  expect((await saved).status()).toBe(200);
}

function shanghaiDate(offset = 0) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const year = Number(parts.find(part => part.type === "year")!.value);
  const month = Number(parts.find(part => part.type === "month")!.value);
  const day = Number(parts.find(part => part.type === "day")!.value);
  return new Date(Date.UTC(year, month - 1, day + offset)).toISOString().slice(0, 10);
}

async function seedApplication(request: APIRequestContext, title: string, action: { title: string; due_date: string | null; due_time?: string | null; status?: string } | null, status = "submitted") {
  const headers = { "X-CSRF-Token": await csrf(request) };
  const jobResponse = await request.post("/api/v1/jobs", { headers, data: { idempotency_key: randomUUID(), title, company: "合成工作台科技", location: "上海", jd_text: "必须熟悉 Python。" } });
  expect(jobResponse.status()).toBe(201);
  const job = await jobResponse.json();
  const created = await request.post(applications, { headers, data: { idempotency_key: randomUUID(), job_id: job.id, job_revision: job.revision } });
  expect(created.status()).toBe(201);
  const application = await created.json();
  const saved = await request.put(`${applications}/${application.id}`, { headers, data: {
    revision: application.revision, idempotency_key: randomUUID(), status, submitted_at: null, notes: "仅用于隔离数据库的合成验收记录。",
    next_action: action ? { title: action.title, notes: "合成行动", due_date: action.due_date, due_time: action.due_time ?? null, status: action.status ?? "pending" } : null,
  } });
  expect(saved.status()).toBe(200);
  return saved.json();
}

test("MVP 真实完整旅程：邀请注册 → PDF → 手工确认画像 → JD 审核 → 匹配双侧证据 → 申请 → 工作台完成 → 重登录", async ({ page, pdf }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const providerMutations: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && /(?:extraction-runs|ai-authorizations|deepseek)/.test(request.url())) providerMutations.push(request.url());
  });
  const account = await invitation();
  await page.goto("/register");
  await page.getByLabel("邮箱地址", { exact: true }).fill(account.email);
  await page.getByLabel("密码", { exact: true }).fill(syntheticPassword);
  await page.getByLabel("邀请码", { exact: true }).fill(account.inviteToken);
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await loginUI(page, account.email);
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "上传第一份简历", exact: true })).toBeVisible();
  expect((await read(page.request, dashboard)).summary.total).toBe(0);
  await screenshot(page, "dashboard-empty");

  const uploaded = await uploadUI(page, pdf, "synthetic-mvp-resume.pdf");
  await page.goto(`/resumes/${uploaded.id}`);
  await expect(page.getByText("文本解析完成", { exact: true }).first()).toBeVisible();
  await screenshot(page, "mvp-resume", [1440]);
  await page.goto("/profile");
  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("合成大学");
  await page.getByLabel("学历", { exact: true }).fill("本科");
  await page.getByLabel("专业", { exact: true }).fill("计算机科学与技术");
  await page.getByLabel("毕业时间", { exact: true }).fill("2027-06");
  await page.getByLabel("毕业时间类型", { exact: true }).selectOption("expected");
  await page.getByRole("button", { name: "添加技能", exact: true }).click();
  await page.getByLabel("技能名称", { exact: true }).fill("Python");
  await page.getByLabel("掌握情况", { exact: true }).fill("熟悉");
  const savedProfile = page.waitForResponse(response => response.url().endsWith("/api/v1/profile") && response.request().method() === "PUT");
  await page.getByRole("button", { name: "保存资料", exact: true }).click();
  expect((await savedProfile).status()).toBe(200);
  await expect(page.locator(".profile-savebar")).toContainText("当前资料已与服务器同步");
  const profile = await read(page.request, "/api/v1/profile");
  expect(profile.skills[0]).toMatchObject({ name: "Python", source: { kind: "manual" } });
  expect(profile.candidates).toEqual([]);
  await screenshot(page, "mvp-profile-manual", [1440]);

  await page.getByRole("link", { name: "职位", exact: true }).first().click();
  await page.getByRole("link", { name: "添加职位", exact: true }).first().click();
  await page.getByLabel("招聘 JD", { exact: true }).fill(syntheticJD);
  await page.getByRole("button", { name: "继续填写基本信息", exact: true }).click();
  await page.getByLabel("岗位名称", { exact: true }).fill("后端开发实习生（MVP 合成）");
  await page.getByLabel("公司名称", { exact: true }).fill("合成闭环科技");
  await page.getByLabel("工作城市", { exact: true }).fill("上海");
  await page.getByLabel("工作类型", { exact: true }).selectOption("internship");
  await page.getByRole("button", { name: "继续审核条件", exact: true }).click();
  const parsedResponse = page.waitForResponse(response => response.url().endsWith("/jobs/parse-preview"));
  await page.getByRole("button", { name: "免费规则整理", exact: true }).click();
  expect((await parsedResponse).status()).toBe(200);
  const parsed = await (await parsedResponse).json();
  expect(parsed.requirements.length).toBeGreaterThanOrEqual(3);
  expect(parsed.requirements.every((item: { review_status: string }) => item.review_status === "pending")).toBe(true);
  for (let index = 0; index < parsed.requirements.length; index++) {
    await page.getByRole("article").nth(index).getByRole("button", { name: "确认条件", exact: true }).click();
  }
  await screenshot(page, "mvp-jd-reviewed", [1440]);
  const createdJob = page.waitForResponse(response => response.url().endsWith("/api/v1/jobs") && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存职位", exact: true }).click();
  expect((await createdJob).status()).toBe(201);
  const job = await (await createdJob).json();
  await expect(page).toHaveURL(new RegExp(`/jobs/${job.id}$`));
  expect(job.review_summary.pending).toBe(0);
  expect(job.requirements.every((item: { review_status: string }) => item.review_status === "confirmed")).toBe(true);
  const matching = page.getByRole("region", { name: "岗位匹配", exact: true });
  const matched = page.waitForResponse(response => response.url().endsWith(`/jobs/${job.id}/matches`) && response.request().method() === "POST");
  await matching.getByRole("button", { name: "开始匹配", exact: true }).click();
  expect((await matched).status()).toBe(200);
  const match = await (await matched).json();
  expect(match.stale).toBe(false);
  expect(match.evaluation.items.find((item: { content: string }) => item.content.includes("Python")).status).toBe("met");
  expect(match.evaluation.items.find((item: { content: string }) => item.content.includes("Java")).status).toBe("unknown");
  const python = matching.getByRole("article").filter({ hasText: "必须熟悉 Python" });
  await python.getByText("查看判断与证据", { exact: true }).click();
  await expect(python.getByRole("heading", { name: "正式画像事实", exact: true })).toBeVisible();
  await expect(python).toContainText("用户填写");
  await expect(python.getByRole("heading", { name: "JD 原文依据", exact: true })).toBeVisible();
  const pythonRequirement = job.requirements.find((item: { content: string }) => item.content.includes("Python"));
  expect(pythonRequirement.evidence_text).toContain("必须熟悉 Python");
  expect(syntheticJD.slice(pythonRequirement.source_start, pythonRequirement.source_end)).toBe(pythonRequirement.evidence_text);
  await expect(python.locator("blockquote")).toHaveText(pythonRequirement.evidence_text);
  await expect(matching).toContainText("未发现证据不等于不具备能力");
  await screenshot(page, "mvp-match-evidence", [1440, 768, 390], python);
  await page.setViewportSize({ width: 1440, height: 1000 });

  const matchCheckbox = page.getByRole("checkbox", { name: /关联这份匹配分析/ });
  await expect(matchCheckbox).toBeVisible();
  await matchCheckbox.check();
  const prepared = page.waitForResponse(response => response.url().endsWith(applications) && response.request().method() === "POST");
  await page.getByRole("button", { name: "准备投递", exact: true }).click();
  expect((await prepared).status()).toBe(201);
  const application = await (await prepared).json();
  expect(application.match.id).toBe(match.id);
  await expect(page).toHaveURL(new RegExp(`/applications/${application.id}$`));
  await expect(page.getByLabel("申请状态", { exact: true })).toHaveValue("preparing");
  await page.getByLabel("申请状态", { exact: true }).selectOption("submitted");
  await page.getByLabel("下一步行动", { exact: true }).fill("整理项目说明并准备面试");
  await page.getByLabel("行动截止日期", { exact: true }).fill(shanghaiDate());
  await page.getByLabel("行动备注", { exact: true }).fill("合成练习：复盘 Python 项目的接口设计。");
  await saveApplication(page, application.id);
  const submitted = await read(page.request, `${applications}/${application.id}`);
  expect(submitted).toMatchObject({ status: "submitted", submitted_at: null, is_overdue: false, next_action: { due_date: shanghaiDate(), due_time: null, status: "pending" } });
  await screenshot(page, "mvp-application", [1440, 390]);
  await page.getByRole("link", { name: "工作台", exact: true }).filter({ visible: true }).click();
  const home = await read(page.request, dashboard);
  expect(home.summary).toMatchObject({ total: 1, preparing: 0, submitted: 1 });
  expect(home.actions.today.total).toBe(1);
  expect(home.actions.today.items[0].application_id).toBe(application.id);
  await expect(page.getByRole("article", { name: "整理项目说明并准备面试", exact: true })).toBeVisible();
  await screenshot(page, "dashboard-today");
  await page.setViewportSize({ width: 390, height: 1000 });
  const actionCard = page.getByRole("article", { name: "整理项目说明并准备面试", exact: true });
  const completed = page.waitForResponse(response => response.url().endsWith(`${applications}/${application.id}`) && response.request().method() === "PUT");
  await actionCard.getByRole("button", { name: "完成行动", exact: true }).click();
  expect((await completed).status()).toBe(200);
  await expect.poll(async () => (await read(page.request, dashboard)).actions.today.total).toBe(0);
  expect((await read(page.request, dashboard)).actions.completed.total).toBe(1);
  const final = await read(page.request, `${applications}/${application.id}`);
  expect(final).toMatchObject({ status: "submitted", is_overdue: false, next_action: { status: "completed", due_time: null } });
  await page.locator(".workbench-history > summary").click();
  await expect(page.getByRole("region", { name: "已完成", exact: true }).getByRole("article", { name: "整理项目说明并准备面试", exact: true })).toBeVisible();
  await screenshot(page, "dashboard-completed", [390]);
  await logoutUI(page);
  await loginUI(page, account.email);
  expect((await read(page.request, dashboard)).actions.completed.total).toBe(1);
  await page.goto(`/applications/${application.id}`);
  await expect(page.getByLabel("申请状态", { exact: true })).toHaveValue("submitted");
  await expect(page.getByLabel("下一步行动", { exact: true })).toHaveValue("整理项目说明并准备面试");
  await expect(page.getByRole("button", { name: "恢复待完成", exact: true })).toBeVisible();
  expect((await read(page.request, "/api/v1/resumes"))[0].id).toBe(uploaded.id);
  expect((await read(page.request, "/api/v1/profile")).skills[0].name).toBe("Python");
  expect((await read(page.request, `/api/v1/jobs/${job.id}`)).review_summary.pending).toBe(0);
  expect((await read(page.request, `/api/v1/jobs/${job.id}/matches/latest`)).id).toBe(match.id);
  expect(providerMutations).toEqual([]);
  await writeFile(path.join(output, "synthetic-mvp-journey.json"), JSON.stringify({ resume_id: uploaded.id, profile_revision: profile.revision, job_id: job.id, match, application: final, dashboard: await read(page.request, dashboard), real_model_requests: 0 }, null, 2));
});

test("真实工作台行动分组、逾期完成取消恢复、筛选排序与移动端直达申请", async ({ page }) => {
  test.setTimeout(150_000);
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const overdue = await seedApplication(page.request, "逾期申请（合成）", { title: "跟进已逾期的面试安排", due_date: shanghaiDate(-1) });
  const today = await seedApplication(page.request, "今日申请（合成）", { title: "复盘今天的技术面试", due_date: shanghaiDate() }, "interview");
  const upcoming = await seedApplication(page.request, "七天内申请（合成）", { title: "七天后提交作品集", due_date: shanghaiDate(7) }, "preparing");
  const undated = await seedApplication(page.request, "未定日期申请（合成）", { title: "核实 Offer 条款", due_date: null }, "offer");
  const completed = await seedApplication(page.request, "完成行动申请（合成）", { title: "已完成的笔试准备", due_date: shanghaiDate(-2), status: "completed" }, "assessment");
  const cancelled = await seedApplication(page.request, "取消行动申请（合成）", { title: "已经取消的跟进", due_date: shanghaiDate(-3), status: "cancelled" }, "closed");
  const none = await seedApplication(page.request, "无行动申请（合成）", null, "rejected");
  // A favorite without an application remains outside the application counts.
  const headers = { "X-CSRF-Token": await csrf(page.request) };
  const favoriteResponse = await page.request.post("/api/v1/jobs", { headers, data: { idempotency_key: randomUUID(), title: "只收藏未申请（合成）", company: "合成收藏公司", jd_text: "必须熟悉 Python。" } });
  expect(favoriteResponse.status()).toBe(201);
  const favorite = await favoriteResponse.json();
  const marked = await page.request.patch(`/api/v1/jobs/${favorite.id}/favorite`, { headers, data: { revision: favorite.revision, is_favorite: true } });
  expect(marked.status()).toBe(200);
  await page.goto("/");
  const initial = await read(page.request, dashboard);
  expect(initial.summary).toMatchObject({ total: 7, preparing: 1, submitted: 1, assessment_interview: 2, offer: 1, ended: 2 });
  for (const [key, title] of Object.entries({ overdue: "跟进已逾期的面试安排", today: "复盘今天的技术面试", upcoming: "七天后提交作品集", undated: "核实 Offer 条款", completed: "已完成的笔试准备", cancelled: "已经取消的跟进" })) {
    expect(initial.actions[key].total).toBe(1);
    expect(initial.actions[key].items[0].next_action.title).toBe(title);
  }
  const overdueGroup = page.getByRole("region", { name: "已逾期", exact: true });
  await expect(overdueGroup.getByRole("article", { name: "跟进已逾期的面试安排", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "今天需要完成", exact: true })).toContainText("复盘今天的技术面试");
  await expect(page.getByRole("region", { name: "未来七天", exact: true })).toContainText("七天后提交作品集");
  await expect(page.getByRole("region", { name: "未设截止日期", exact: true })).toContainText("核实 Offer 条款");
  await screenshot(page, "dashboard-overdue");

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("link", { name: "投递进度", exact: true }).filter({ visible: true }).click();
  await expect(page.locator(".application-row")).toHaveCount(7);
  await page.getByLabel("下一步行动筛选", { exact: true }).selectOption("pending");
  await expect(page.locator(".application-row")).toHaveCount(4);
  await page.getByLabel("逾期筛选", { exact: true }).selectOption("true");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await expect(page.locator(".application-row")).toContainText("逾期申请（合成）");
  await screenshot(page, "applications-filtered");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel("逾期筛选", { exact: true }).selectOption("");
  await page.getByLabel("下一步行动筛选", { exact: true }).selectOption("completed");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await expect(page.locator(".application-row")).toContainText("完成行动申请（合成）");
  await page.getByLabel("下一步行动筛选", { exact: true }).selectOption("cancelled");
  await expect(page.locator(".application-row")).toContainText("取消行动申请（合成）");
  await page.getByLabel("下一步行动筛选", { exact: true }).selectOption("none");
  await expect(page.locator(".application-row")).toContainText("无行动申请（合成）");
  await page.getByLabel("下一步行动筛选", { exact: true }).selectOption("");
  await page.getByLabel("申请状态筛选", { exact: true }).selectOption("offer");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await expect(page.locator(".application-row")).toContainText("未定日期申请（合成）");
  await page.getByLabel("申请状态筛选", { exact: true }).selectOption("");
  await page.getByLabel("搜索申请", { exact: true }).fill("今日申请");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await expect(page.locator(".application-row")).toContainText("今日申请（合成）");
  await page.getByLabel("搜索申请", { exact: true }).fill("确定不存在的公司");
  await expect(page.getByRole("heading", { name: "没有找到符合筛选的申请", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清除申请筛选", exact: true }).click();
  await expect(page.locator(".application-row")).toHaveCount(7);
  await page.getByLabel("申请排序", { exact: true }).selectOption("next_action_asc");
  const sorted = await read(page.request, `${applications}?sort=next_action_asc`);
  await expect(page.locator(".application-row").first()).toHaveAttribute("href", new RegExp(`/applications/${sorted.items[0].id}(?:\\?|$)`));

  await page.goto("/");
  let card = page.getByRole("article", { name: "跟进已逾期的面试安排", exact: true });
  await card.getByRole("button", { name: "完成行动", exact: true }).focus();
  const done = page.waitForResponse(response => response.url().endsWith(`${applications}/${overdue.id}`) && response.request().method() === "PUT");
  await page.keyboard.press("Enter");
  expect((await done).status()).toBe(200);
  await expect(overdueGroup.getByRole("article")).toHaveCount(0);
  expect((await read(page.request, dashboard)).actions.completed.total).toBe(2);
  expect((await read(page.request, `${applications}/${overdue.id}`)).is_overdue).toBe(false);
  await page.locator(".workbench-history > summary").click();
  card = page.getByRole("article", { name: "跟进已逾期的面试安排", exact: true });
  await card.getByRole("button", { name: "恢复待完成", exact: true }).click();
  await expect(overdueGroup.getByRole("article")).toHaveCount(1);
  await card.getByRole("button", { name: "取消行动", exact: true }).click();
  const cancelDialog = page.getByRole("alertdialog");
  await expect(cancelDialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cancelDialog).toBeHidden();
  expect((await read(page.request, `${applications}/${overdue.id}`)).next_action.status).toBe("pending");
  await card.getByRole("button", { name: "取消行动", exact: true }).click();
  await cancelDialog.getByRole("button", { name: "确认取消行动", exact: true }).click();
  await expect(overdueGroup.getByRole("article")).toHaveCount(0);
  expect((await read(page.request, dashboard)).actions.cancelled.total).toBe(2);
  expect((await read(page.request, `${applications}/${overdue.id}`)).is_overdue).toBe(false);
  await card.getByRole("button", { name: "恢复待完成", exact: true }).click();
  await expect(overdueGroup.getByRole("article")).toHaveCount(1);

  await page.setViewportSize({ width: 390, height: 1000 });
  await card.getByRole("link", { name: "修改下一步", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${overdue.id}(?:#.*)?$`));
  await page.getByLabel("行动截止日期", { exact: true }).fill(shanghaiDate(2));
  await saveApplication(page, overdue.id);
  await page.getByRole("link", { name: "工作台", exact: true }).filter({ visible: true }).click();
  await expect(page.getByRole("region", { name: "未来七天", exact: true })).toContainText("跟进已逾期的面试安排");
  const updated = await read(page.request, dashboard);
  expect(updated.actions.overdue.total).toBe(0);
  expect(updated.actions.upcoming.total).toBe(2);
  expect(updated.summary.total).toBe(7);
  await screenshot(page, "dashboard-mobile", [390]);
  await writeFile(path.join(output, "synthetic-workbench-groups.json"), JSON.stringify({ initial, updated, application_ids: [overdue.id, today.id, upcoming.id, undated.id, completed.id, cancelled.id, none.id] }, null, 2));
});

test("真实申请列表分页与筛选历史恢复，工作台会话失效", async ({ page }) => {
  test.setTimeout(120_000);
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  for (let index = 1; index <= 13; index++) await seedApplication(page.request, `分页合成岗位 ${String(index).padStart(2, "0")}`, null, "preparing");
  await page.goto("/applications");
  await expect(page.locator(".application-row")).toHaveCount(12);
  await page.getByRole("button", { name: "下一页申请", exact: true }).click();
  await expect(page.locator(".application-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "下一页申请", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "上一页申请", exact: true }).click();
  await expect(page.locator(".application-row")).toHaveCount(12);
  await page.getByLabel("搜索申请", { exact: true }).fill("分页合成岗位 07");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await page.locator(".application-row").click();
  await expect(page.getByRole("heading", { name: "分页合成岗位 07", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("搜索申请", { exact: true })).toHaveValue("分页合成岗位 07");
  await expect(page.locator(".application-row")).toHaveCount(1);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
  await control("expire-session", { email: account.email });
  await page.reload();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  expect((await page.request.get(dashboard)).status()).toBe(401);
});

test("工作台真实并发冲突可刷新恢复，已提交后响应丢失按原请求重试", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const application = await seedApplication(page.request, "工作台恢复验证（合成）", { title: "核对面试时间", due_date: shanghaiDate(-1) });
  await page.goto("/");
  const card = page.getByRole("article", { name: "核对面试时间", exact: true });
  await expect(card).toBeVisible();
  const changed = await page.request.put(`${applications}/${application.id}`, { headers: { "X-CSRF-Token": await csrf(page.request) }, data: {
    revision: application.revision, idempotency_key: randomUUID(), status: application.status, submitted_at: application.submitted_at,
    notes: "另一页面已核实联系安排，请保留这段备注。", next_action: application.next_action,
  } });
  expect(changed.status()).toBe(200);
  await card.getByRole("button", { name: "完成行动", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("申请或行动已在其他页面更新");
  await expect(card.getByRole("button", { name: "完成行动", exact: true })).toBeDisabled();
  expect((await read(page.request, `${applications}/${application.id}`)).next_action.status).toBe("pending");
  await page.getByRole("button", { name: "刷新工作台", exact: true }).first().click();
  await expect(card.getByRole("button", { name: "完成行动", exact: true })).toBeEnabled();

  // Transport fault only: the real API commits, then the browser loses the
  // response. No synthetic HTTP payload replaces the application response.
  let interrupted = false;
  await page.route(`**${applications}/${application.id}`, async route => {
    if (route.request().method() === "PUT" && !interrupted) {
      interrupted = true;
      const committed = await route.fetch();
      expect(committed.status()).toBe(200);
      await route.abort("failed");
    } else await route.continue();
  });
  const before = await read(page.request, `${applications}/${application.id}/events`);
  await card.getByRole("button", { name: "完成行动", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("操作结果可能尚未确认");
  const committed = await read(page.request, `${applications}/${application.id}`);
  expect(committed.next_action.status).toBe("completed");
  expect(committed.notes).toBe("另一页面已核实联系安排，请保留这段备注。");
  await card.getByRole("button", { name: "完成行动", exact: true }).click();
  await expect(page.getByRole("region", { name: "已逾期", exact: true }).getByRole("article")).toHaveCount(0);
  expect((await read(page.request, `${applications}/${application.id}`)).revision).toBe(committed.revision);
  expect((await read(page.request, `${applications}/${application.id}/events`)).total).toBe(before.total + 1);
  expect((await read(page.request, dashboard)).actions.completed.total).toBe(1);
});
