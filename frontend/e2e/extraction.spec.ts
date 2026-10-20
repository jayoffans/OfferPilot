import { mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import type { Page } from "@playwright/test";
import type { CareerProfile, ExtractionCandidates, ExtractionRun } from "../src/lib/api";
import { control, createAccount, csrf, expect, loginUI, logoutUI, test, uploadUI } from "./fixtures";

// Independent public oracle for the PDF fixture. The metadata hashes and
// offsets must refer to the text actually parsed after the upload, rather than
// trusting model-authored page numbers or the candidate's quote alone.
const expectedPdfText = [
  "合成简历（仅用于测试）",
  "教育：示例大学；本科；计算机科学；2022年9月入学；预计毕业：2026年6月。",
  "技能：Python。",
  "技能：JavaScript。",
  "尚未掌握 Java。",
  "计划学习 Go，目前尚未掌握。",
  "项目：课程选课系统；角色：后端开发；2024年3月至2024年6月；技术栈：Python；工作：编写课程查询接口。",
  "实习：示例科技；岗位：后端实习生；2025年7月至2025年9月；职责：维护测试脚本；成果：补充接口测试用例。",
// The PDF puts each line 24 points apart; PyMuPDF preserves one blank line
// between those blocks. Offsets must use that parsed layout, not source text.
].join("\n\n");
const sha256 = (content: Buffer | string) => createHash("sha256").update(content).digest("hex");
function expectEmptyFormalProfile(value: CareerProfile) {
  for (const group of ["education", "skills", "projects", "experiences"] as const) expect(value[group]).toEqual([]);
}

async function syntheticPdf() { const response = await control("extraction-pdf"); return Buffer.from(await response.arrayBuffer()); }
async function createRun(page: Page, resumeId: string) {
  await page.goto(`/resumes/${resumeId}`);
  const response = page.waitForResponse(item => item.url().endsWith(`/resumes/${resumeId}/extraction-runs`) && item.request().method() === "POST");
  await page.getByRole("button", { name: "创建 Mock 测试任务", exact: true }).click();
  const created = await response; expect(created.status()).toBe(202);
  return await created.json() as ExtractionRun;
}
async function waitRun(page: Page, id: string, status: ExtractionRun["status"]) {
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/extraction-runs/${id}`);
    expect(response.status()).toBe(200); return (await response.json() as ExtractionRun).status;
  }).toBe(status);
  return await (await page.request.get(`/api/v1/extraction-runs/${id}`)).json() as ExtractionRun;
}
async function profile(page: Page) { return await (await page.request.get("/api/v1/profile")).json() as CareerProfile; }

test.afterEach(async () => { await control("extraction-mode", { mode: "default" }); });

test("合成中文 PDF → Mock 持久任务 → 原文定位候选 → S2-1 用户确认", async ({ page }) => {
  const account = await createAccount(page.request); await loginUI(page, account.email);
  const pdf = await syntheticPdf();
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
  const uploaded = await uploadUI(page, pdf, "合成中文简历-抽取测试.pdf");
  const lifecycle = await page.request.get(`/api/v1/resumes/${uploaded.id}/lifecycle`);
  expect(lifecycle.status()).toBe(200);
  expect(await lifecycle.json()).toMatchObject({ processing_status: "parsed", lifecycle_status: "active" });
  const downloaded = await page.request.get(`/api/v1/resumes/${uploaded.id}/download`);
  expect(downloaded.status()).toBe(200);
  expect(sha256(await downloaded.body())).toBe(sha256(pdf));
  expectEmptyFormalProfile(await profile(page));
  const createdRequest = page.waitForRequest(item => item.url().endsWith(`/resumes/${uploaded.id}/extraction-runs`) && item.method() === "POST");
  const run = await createRun(page, uploaded.id);
  const originalBody = (await createdRequest).postDataJSON();
  const done = await waitRun(page, run.id, "succeeded");
  expect(done.test_result).toBe(true); expect(done.mode).toBe("mock");
  const response = await page.request.get(`/api/v1/extraction-runs/${run.id}/candidates`);
  expect(response.status()).toBe(200);
  const result = await response.json() as ExtractionCandidates;
  expect(result.candidates.length).toBeGreaterThan(4);
  expect(done.candidate_count).toBe(result.candidates.length);
  expect(result.candidates.every(candidate => candidate.status === "pending" && candidate.extraction_metadata?.test_result)).toBe(true);
  expect(new Set(result.candidates.map(candidate => candidate.category))).toEqual(new Set(["education", "skill", "project", "experience"]));
  for (const candidate of result.candidates) {
    const metadata = candidate.extraction_metadata!;
    expect(metadata.provider).toBe("mock");
    expect(metadata.schema_version).toBe("2.0");
    expect(metadata.source_file_sha256).toBe(sha256(pdf));
    expect(metadata.source_text_sha256).toBe(sha256(expectedPdfText));
    for (const field of metadata.fields) {
      const locations = field.locations ?? [];
      expect(locations.length).toBeGreaterThan(0);
      for (const location of locations) {
        expect(location.page).toBe(1);
        expect(expectedPdfText.slice(location.start, location.end)).toBe(field.source_text);
      }
    }
  }
  const replayed = await page.request.post(`/api/v1/resumes/${uploaded.id}/extraction-runs`, { headers: { "X-CSRF-Token": await csrf(page.request) }, data: originalBody });
  expect(replayed.status()).toBe(202); expect((await replayed.json()).id).toBe(run.id);
  expect((await profile(page)).candidates.map(candidate => candidate.id).sort()).toEqual(result.candidates.map(candidate => candidate.id).sort());
  expectEmptyFormalProfile(await profile(page));
  await page.reload();
  await expect(page.getByText("候选已生成", { exact: true })).toBeVisible();
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 960 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await mkdir(path.resolve("artifacts/screenshots"), { recursive: true });
    await page.screenshot({ path: path.resolve(`artifacts/screenshots/extraction-task-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("link", { name: "查看并审核候选", exact: true }).click();
  await page.reload();
  expectEmptyFormalProfile(await profile(page));
  await logoutUI(page); await loginUI(page, account.email); await page.goto("/profile");
  expect((await profile(page)).candidates.map(candidate => candidate.id).sort()).toEqual(result.candidates.map(candidate => candidate.id).sort());
  expectEmptyFormalProfile(await profile(page));
  const python = result.candidates.find(candidate => candidate.category === "skill" && "name" in candidate.value && candidate.value.name === "Python")!;
  const java = result.candidates.find(candidate => candidate.category === "skill" && "name" in candidate.value && candidate.value.name === "Java")!;
  const go = result.candidates.find(candidate => candidate.category === "skill" && "name" in candidate.value && candidate.value.name === "Go")!;
  const pythonCard = page.locator(`[data-candidate-id="${python.id}"]`);
  await expect(pythonCard).toContainText("Mock 测试候选");
  await pythonCard.getByText("查看抽取字段、归属与原文位置", { exact: true }).click();
  await expect(pythonCard.getByText(/服务器原文定位：第 1 页/)).toBeVisible();
  await expect(page.locator(`[data-candidate-id="${java.id}"]`).getByRole("button", { name: "确认事实", exact: true })).toBeDisabled();
  await expect(page.locator(`[data-candidate-id="${go.id}"]`).getByRole("button", { name: "确认事实", exact: true })).toBeDisabled();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.resolve("artifacts/screenshots/extraction-candidates-1440.png"), fullPage: true });
  await pythonCard.getByRole("button", { name: "确认事实", exact: true }).click();
  await expect(page.getByText("事实已确认并加入正式资料。", { exact: true })).toBeVisible();
  const confirmed = await profile(page);
  expect(confirmed.skills.map(skill => skill.name)).toEqual(["Python"]);
  for (const group of ["education", "projects", "experiences"] as const) expect(confirmed[group]).toEqual([]);
  expect(confirmed.skills[0].source.test_result).toBe(true);
  await page.getByText("查看来源与证据", { exact: true }).click();
  await expect(page.getByText(/来源为 Mock 测试结果，经用户主动确认或修订/)).toBeVisible();
  await page.reload(); await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("Python");
  await page.locator(`[data-candidate-id="${java.id}"]`).getByRole("button", { name: "拒绝候选", exact: true }).click();
  await expect(page.getByText("候选已拒绝，未写入正式资料。", { exact: true })).toBeVisible();
  const reviewed = await profile(page);
  expect(reviewed.skills.map(skill => skill.name)).toEqual(["Python"]);
  expect(reviewed.candidates.find(candidate => candidate.id === java.id)?.status).toBe("rejected");
  expect(reviewed.candidates.filter(candidate => candidate.status === "pending").length).toBe(result.candidates.length - 2);
  await page.getByText(/^已审核记录/).click();
  await page.screenshot({ path: path.resolve("artifacts/screenshots/extraction-review-isolation-1440.png"), fullPage: true });
  await logoutUI(page); await loginUI(page, account.email); await page.goto("/profile");
  await expect(page.getByLabel("技能名称", { exact: true })).toHaveValue("Python");
  const restored = await profile(page);
  expect(restored.candidates.find(candidate => candidate.id === python.id)?.status).toBe("confirmed");
  expect(restored.candidates.find(candidate => candidate.id === java.id)?.status).toBe("rejected");
  expect(restored.skills.map(skill => skill.name)).toEqual(["Python"]);
});

test("排队任务可取消，幂等重放不重复创建候选，匿名与他人不能查询", async ({ page, browser }) => {
  await control("extraction-mode", { mode: "paused" });
  const account = await createAccount(page.request); await loginUI(page, account.email);
  const uploaded = await uploadUI(page, await syntheticPdf());
  const createdRequest = page.waitForRequest(item => item.url().endsWith(`/resumes/${uploaded.id}/extraction-runs`) && item.method() === "POST");
  const run = await createRun(page, uploaded.id);
  const originalBody = (await createdRequest).postDataJSON();
  const repeated = await page.request.post(`/api/v1/resumes/${uploaded.id}/extraction-runs`, { headers: { "X-CSRF-Token": await csrf(page.request) }, data: originalBody });
  expect(repeated.status()).toBe(202); expect((await repeated.json()).id).toBe(run.id);
  await expect(page.getByText("等待处理", { exact: true })).toBeVisible();
  const current = await (await page.request.get(`/api/v1/resumes/${uploaded.id}/extraction`)).json();
  const forbidden = await page.request.post(`/api/v1/extraction-runs/${run.id}/cancel`, { data: { revision: run.revision } }); expect(forbidden.status()).toBe(403);
  const context = await browser.newContext(); const other = await context.newPage();
  const anonymous = await other.request.get(`/api/v1/extraction-runs/${run.id}`); expect(anonymous.status()).toBe(401);
  const second = await createAccount(other.request); await loginUI(other, second.email);
  for (const route of [`extraction-runs/${run.id}`, `extraction-runs/${run.id}/candidates`, `resumes/${uploaded.id}/extraction`]) expect((await other.request.get(`/api/v1/${route}`)).status()).toBe(404);
  expect(current.source_version).toBe(run.source_version);
  await context.close();
  await page.getByRole("button", { name: "取消测试任务", exact: true }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  await control("extraction-mode", { mode: "default" });
  await waitRun(page, run.id, "cancelled"); expect((await profile(page)).candidates).toEqual([]);
  const token = await csrf(page.request);
  const cancelled = await page.request.post(`/api/v1/extraction-runs/${run.id}/retry`, { headers: { "X-CSRF-Token": token }, data: { revision: 1 } }); expect(cancelled.status()).toBe(409);
});

test("无效 JSON 失败可查询并有限重试，不写正式画像", async ({ page }) => {
  await control("extraction-mode", { mode: "invalid" });
  const account = await createAccount(page.request); await loginUI(page, account.email);
  const uploaded = await uploadUI(page, await syntheticPdf()); const run = await createRun(page, uploaded.id);
  const failed = await waitRun(page, run.id, "failed");
  expect(failed.error_code).toBeTruthy(); expect(failed.call_count).toBeLessThanOrEqual(failed.max_calls);
  expect((await profile(page)).candidates).toEqual([]); expect((await profile(page)).skills).toEqual([]);
  await page.reload(); await expect(page.getByText("抽取失败", { exact: true })).toBeVisible();
  await control("extraction-mode", { mode: "default" });
  await page.getByRole("button", { name: "有限重试测试任务", exact: true }).click();
  await waitRun(page, run.id, "succeeded");
  const candidates = (await profile(page)).candidates;
  expect(candidates.length).toBeGreaterThan(4); expect((await profile(page)).skills).toEqual([]);
  const repeated = await page.request.post(`/api/v1/extraction-runs/${run.id}/retry`, { headers: { "X-CSRF-Token": await csrf(page.request) }, data: { revision: failed.revision } });
  expect(repeated.status()).toBe(409); expect((await profile(page)).candidates.length).toBe(candidates.length);
});

test("运行中取消及删除阻止迟到结果落库", async ({ page }) => {
  const account = await createAccount(page.request); await loginUI(page, account.email);
  for (const action of ["cancel", "delete"] as const) {
    await control("extraction-mode", { mode: "blocked" });
    const uploaded = await uploadUI(page, await syntheticPdf(), `synthetic-${action}.pdf`);
    const run = await createRun(page, uploaded.id); await waitRun(page, run.id, "running");
    await expect(page.getByText("正在处理", { exact: true })).toBeVisible();
    await expect.poll(async () => (await (await control("extraction-state")).json() as { provider_started: boolean }).provider_started).toBe(true);
    if (action === "cancel") {
      await page.getByRole("button", { name: "取消测试任务", exact: true }).click();
      await waitRun(page, run.id, "cancelled");
    } else {
      await page.getByRole("button", { name: "删除简历", exact: true }).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "确认删除", exact: true }).click();
      await expect(page.getByRole("heading", { name: "这份简历已删除", exact: true })).toBeVisible();
    }
    await control("extraction-mode", { mode: "default" });
    await expect.poll(async () => (await (await control("extraction-state")).json() as { batch_completed: boolean }).batch_completed).toBe(true);
    await expect.poll(async () => (await profile(page)).candidates.length).toBe(0);
    expect((await profile(page)).skills).toEqual([]);
    if (action === "cancel") await waitRun(page, run.id, "cancelled");
    else {
      const persisted = await waitRun(page, run.id, "cancelled");
      expect(persisted.error_code).toBe("resume_deleted"); expect(persisted.candidate_count).toBe(0);
      const late = await page.request.get(`/api/v1/extraction-runs/${run.id}/candidates`);
      expect(late.status()).toBe(200); expect((await late.json()).candidates).toEqual([]);
    }
  }
});
