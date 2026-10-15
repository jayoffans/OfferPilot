import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { APIRequestContext, Page } from "@playwright/test";
import { test, expect, control, createAccount, loginUI, csrf, uploadUI } from "./fixtures";

const output = path.resolve("artifacts/s3-2");
test.beforeEach(async () => { await control("reset-throttle"); });

async function readProfile(request: APIRequestContext) {
  const response = await request.get("/api/v1/profile");
  expect(response.status()).toBe(200);
  return response.json();
}

async function savedProfile(page: Page) {
  const response = page.waitForResponse(r => r.url().endsWith("/api/v1/profile") && r.request().method() === "PUT");
  await page.getByRole("button", { name: "保存资料", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(page.locator(".profile-savebar")).toContainText("当前资料已与服务器同步");
}

async function screenshots(page: Page, name: string) {
  await mkdir(output, { recursive: true });
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.getByRole("region", { name: "岗位匹配", exact: true }).evaluate(element => element.scrollIntoView({ block: "start" }));
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
    await page.screenshot({ path: path.join(output, `${name}-${width}-viewport.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

async function createJob(request: APIRequestContext, confirmed = false) {
  const requirements = [
    { type: "education", content: "本科在读", priority: "must" },
    { type: "graduation", content: "2027届毕业生", priority: "must" },
    { type: "technical_skill", content: "必须熟悉 Python", priority: "must" },
    { type: "technical_skill", content: "熟悉 Java 优先", priority: "preferred" },
  ];
  const jd = requirements.map(r => r.content).join("\n");
  const response = await request.post("/api/v1/jobs", {
    headers: { "X-CSRF-Token": await csrf(request) },
    data: {
      idempotency_key: randomUUID(), title: "后端开发实习生（匹配合成样本）", company: "合成科技",
      location: "上海", salary: "200-300元/天", work_type: "internship", jd_text: jd,
      requirements: requirements.map((r, i) => ({ ...r, evidence_text: r.content, review_status: confirmed && i < 3 ? "confirmed" : "pending" })),
    },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

test("可信匹配真实闭环：审核JD、逐项证据、修改画像失效与重新计算", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.goto("/profile");
  await page.getByRole("button", { name: "添加教育经历", exact: true }).click();
  await page.getByLabel("学校", { exact: true }).fill("合成大学");
  await page.getByLabel("学历", { exact: true }).fill("本科");
  await page.getByLabel("专业", { exact: true }).fill("计算机科学与技术");
  await page.getByLabel("毕业时间", { exact: true }).fill("2027-06");
  await page.getByLabel("毕业时间类型", { exact: true }).selectOption("expected");
  await page.getByRole("button", { name: "添加技能", exact: true }).click();
  await page.getByLabel("技能名称", { exact: true }).fill("Python 3");
  await page.getByLabel("掌握情况", { exact: true }).fill("熟悉");
  await savedProfile(page);
  const profile = await readProfile(page.request);
  const job = await createJob(page.request);
  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("button", { name: "编辑职位", exact: true }).click();
  for (let index = 0; index < 3; index++) {
    await page.getByRole("article").nth(index).getByRole("button", { name: "确认条件", exact: true }).click();
  }
  const saved = page.waitForResponse(r => r.url().endsWith(`/api/v1/jobs/${job.id}`) && r.request().method() === "PUT");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  expect((await saved).status()).toBe(200);
  const region = page.getByRole("region", { name: "岗位匹配", exact: true });
  const calculated = page.waitForResponse(r => r.url().endsWith(`/jobs/${job.id}/matches`) && r.request().method() === "POST");
  await region.getByRole("button", { name: "开始匹配", exact: true }).click();
  const initial = await (await calculated).json();
  expect(initial.profile_revision).toBe(profile.revision);
  expect(initial.stale).toBe(false);
  expect(initial.evaluation.items.filter((item: { scope: string }) => item.scope === "qualification")).toHaveLength(3);
  expect(initial.evaluation.excluded_requirements).toHaveLength(1);
  expect(initial.evaluation.items.find((item: { type: string }) => item.type === "technical_skill").status).toBe("met");
  expect(initial.evaluation.conclusion).not.toBe("hard_conflict");
  await expect(region).toContainText("匹配结果是辅助决策，不是录用概率。");
  await region.getByText("查看判断与证据", { exact: true }).first().click();
  await expect(region).toContainText("本科");
  await expect(region).toContainText("2027");
  await screenshots(page, "matching-evidence");

  await page.goto("/profile");
  await page.getByLabel("毕业时间", { exact: true }).fill("2028-06");
  await savedProfile(page);
  await page.goto(`/jobs/${job.id}`);
  await expect(region).toContainText("匹配依据已变更");
  await screenshots(page, "matching-stale");
  const recalculated = page.waitForResponse(r => r.url().endsWith(`/jobs/${job.id}/matches`) && r.request().method() === "POST");
  await region.getByRole("button", { name: "重新计算匹配", exact: true }).click();
  const changed = await (await recalculated).json();
  expect(changed.id).not.toBe(initial.id);
  expect(changed.evaluation.conclusion).toBe("hard_conflict");
  expect(changed.evaluation.items.find((item: { type: string }) => item.type === "graduation").status).toBe("unmet");
  await expect(region.getByRole("heading", { name: "存在明确硬性条件冲突", exact: true })).toBeVisible();
  await expect(region.getByText("匹配依据已变更", { exact: true })).toHaveCount(0);
  await screenshots(page, "matching-hard-conflict");
  const headers = { "X-CSRF-Token": await csrf(page.request) };
  const repeated = await page.request.post(`/api/v1/jobs/${job.id}/matches`, {
    headers, data: { profile_revision: changed.profile_revision, job_revision: changed.job_revision, idempotency_key: randomUUID() },
  });
  expect(repeated.status()).toBe(200);
  expect((await repeated.json()).id).toBe(changed.id);
  const old = await page.request.get(`/api/v1/jobs/${job.id}/matches/${initial.id}`);
  expect(old.status()).toBe(200);
  expect((await old.json()).stale).toBe(true);
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, "synthetic-match-example.json"), JSON.stringify({ initial, after_profile_change: changed }, null, 2));
});

test("匹配输入门槛、未确认候选排除、JD版本改变与安全请求", async ({ page, pdf, browser }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  const job = await createJob(page.request, true);
  await page.goto(`/jobs/${job.id}`);
  const region = page.getByRole("region", { name: "岗位匹配", exact: true });
  await expect(region).toContainText("画像");
  await expect(region.getByRole("button", { name: "开始匹配", exact: true })).toBeDisabled();
  const source = await uploadUI(page, pdf, "matching-pending-only.synthetic.pdf");
  await control(`resume/${source.id}/candidates`);
  const pendingProfile = await readProfile(page.request);
  expect(pendingProfile.skills).toEqual([]);
  expect(pendingProfile.candidates.length).toBeGreaterThan(0);
  const headers = { "X-CSRF-Token": await csrf(page.request) };
  const formal = await page.request.put("/api/v1/profile", {
    headers, data: { revision: pendingProfile.revision, education: [{ school: "合成学校" }], skills: [], projects: [], experiences: [] },
  });
  expect(formal.status()).toBe(200);
  const profile = await formal.json();
  const body = { profile_revision: profile.revision, job_revision: job.revision, idempotency_key: randomUUID() };
  expect((await page.request.post(`/api/v1/jobs/${job.id}/matches`, { data: body })).status()).toBe(403);
  expect((await page.request.post(`/api/v1/jobs/${job.id}/matches`, { headers, data: { ...body, profile: { skills: [{ name: "Python" }] } } })).status()).toBe(422);
  const result = await page.request.post(`/api/v1/jobs/${job.id}/matches`, { headers, data: body });
  expect([200, 201]).toContain(result.status());
  const match = await result.json();
  expect(match.evaluation.items.find((item: { type: string }) => item.type === "technical_skill").status).toBe("unknown");
  expect(match.evaluation.conclusion).toBe("verify_first");
  const changedJd = await page.request.put(`/api/v1/jobs/${job.id}`, {
    headers, data: {
      revision: job.revision, title: job.title, jd_text: "必须熟悉 Go。", work_type: "internship",
      requirements: job.requirements.map((r: { id: string; type: string; content: string; priority: string; evidence_text: string; source_start: number; source_jd_version: number }) => ({
        id: r.id, type: r.type, content: r.content, priority: r.priority, review_status: "pending",
        evidence_text: r.evidence_text, source_start: r.source_start, source_jd_version: r.source_jd_version,
      })),
    },
  });
  expect(changedJd.status()).toBe(200);
  await page.goto(`/jobs/${job.id}`);
  await expect(region).toContainText("匹配依据已变更");
  await expect(region.getByRole("button", { name: "重新计算匹配", exact: true })).toBeDisabled();
  expect((await page.request.post(`/api/v1/jobs/${job.id}/matches`, { headers, data: { ...body, idempotency_key: randomUUID() } })).status()).toBe(409);
  const other = await browser.newContext({ baseURL: test.info().project.use.baseURL as string });
  try {
    const b = await createAccount(other.request);
    const otherPage = await other.newPage();
    await loginUI(otherPage, b.email);
    const otherHeaders = { "X-CSRF-Token": await csrf(other.request) };
    for (const suffix of ["/latest", `/${match.id}`, ""]) expect((await other.request.get(`/api/v1/jobs/${job.id}/matches${suffix}`)).status()).toBe(404);
    expect((await other.request.post(`/api/v1/jobs/${job.id}/matches`, { headers: otherHeaders, data: body })).status()).toBe(404);
    // The same-origin proxy rejects malformed resource paths before the API.
    expect((await other.request.get("/api/v1/jobs/not-a-uuid/matches/latest")).status()).toBe(404);
  } finally { await other.close(); }
});

test("求职偏好独立保存并与资格分别展示，薪资口径未知不换算", async ({ page }) => {
  const account = await createAccount(page.request);
  await loginUI(page, account.email);
  await page.goto("/profile");
  const preferences = page.getByRole("region", { name: "求职偏好", exact: true });
  await preferences.getByLabel("意向城市", { exact: true }).fill("上海、杭州");
  await preferences.getByLabel("岗位方向", { exact: true }).fill("后端开发实习生（匹配合成样本）");
  await preferences.getByRole("checkbox", { name: "实习", exact: true }).check();
  await preferences.getByLabel("期望薪资下限（人民币元）", { exact: true }).fill("250");
  await preferences.getByLabel("期望薪资上限（人民币元）", { exact: true }).fill("350");
  await preferences.getByLabel("薪资周期", { exact: true }).selectOption("day");
  await preferences.getByLabel("薪资税费口径", { exact: true }).selectOption("pre_tax");
  await preferences.getByLabel("每周可实习天数", { exact: true }).fill("3");
  await preferences.getByLabel("可实习月数", { exact: true }).fill("6");
  const saved = page.waitForResponse(r => r.url().endsWith("/api/v1/profile/preferences") && r.request().method() === "PUT");
  await preferences.getByRole("button", { name: "保存求职偏好", exact: true }).click();
  expect((await saved).status()).toBe(200);
  const profile = await readProfile(page.request);
  expect(profile.education).toEqual([]);
  expect(profile.preferences.locations).toEqual(["上海", "杭州"]);
  expect(profile.preferences.salary.period).toBe("day");
  await page.reload();
  await expect(preferences.getByLabel("意向城市", { exact: true })).toHaveValue("上海、杭州");
  const job = await createJob(page.request, true);
  await page.goto(`/jobs/${job.id}`);
  const region = page.getByRole("region", { name: "岗位匹配", exact: true });
  const response = page.waitForResponse(r => r.url().endsWith(`/jobs/${job.id}/matches`) && r.request().method() === "POST");
  await region.getByRole("button", { name: "开始匹配", exact: true }).click();
  const match = await (await response).json();
  expect(match.evaluation.conclusion).toBe("verify_first");
  const items = match.evaluation.items.filter((i: { scope: string }) => i.scope === "preference");
  expect(items.find((i: { type: string }) => i.type === "location_preference").status).toBe("met");
  expect(items.find((i: { type: string }) => i.type === "salary_preference").status).toBe("unknown");
  await expect(region.getByRole("region", { name: "用户求职偏好", exact: true })).toBeVisible();
  await screenshots(page, "matching-preferences");
});

