import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { APIRequestContext, Page } from "@playwright/test";
import { test, expect, control, createAccount, loginUI, uploadUI, csrf, syntheticPassword } from "./fixtures";

const output = path.resolve("artifacts/s5-2");
test.beforeEach(async () => { await control("reset-throttle"); });

async function screenshots(page: Page, name: string) {
  await mkdir(output, { recursive: true });
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

async function read(request: APIRequestContext, url: string) {
  const result = await request.get(url); expect(result.status()).toBe(200); return result.json();
}

async function openSettings(page: Page) {
  await page.getByRole("button", { name: "账户菜单", exact: true }).click();
  await page.getByRole("menuitem", { name: "账户设置", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { name: "账户设置", exact: true })).toBeVisible();
}

async function requestExport(page: Page) {
  await page.getByLabel("验证当前密码", { exact: true }).fill(syntheticPassword);
  const posted = page.waitForResponse(response => response.url().endsWith("/api/v1/me/export") && response.request().method() === "POST");
  await page.getByRole("button", { name: /^(申请导出|重新申请导出)$/ }).click();
  const response = await posted; expect(response.status()).toBe(202);
  const record = await response.json();
  await expect(page.getByRole("button", { name: "下载 ZIP", exact: true }).first()).toBeVisible();
  expect((await read(page.request, `/api/v1/me/export/${record.id}`)).status).toBe("ready");
  return record;
}

async function confirmDeletion(page: Page) {
  await page.getByRole("button", { name: "查看注销说明并继续", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  const final = dialog.getByRole("button", { name: "确认注销并删除数据", exact: true });
  await expect(final).toBeDisabled();
  await dialog.getByLabel("当前密码", { exact: true }).fill(syntheticPassword);
  await dialog.getByLabel("输入“注销账户”以确认", { exact: true }).fill("注销账户");
  await expect(final).toBeDisabled();
  await dialog.getByRole("checkbox").check();
  const posted = page.waitForResponse(response => response.url().endsWith("/api/v1/me/deletion") && response.request().method() === "POST");
  await final.click();
  const response = await posted; expect(response.status()).toBe(202);
  await expect(page).toHaveURL(/\/account-deletion$/);
  // Full navigation deliberately discards the old authenticated document.
  // Read its durable receipt using the short-lived HttpOnly status permission.
  return read(page.request, "/api/v1/me/deletion/status");
}

function inspectZip(file: string) {
  const python = process.env.OFFERPILOT_E2E_PYTHON || path.resolve(
    "../backend/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  const script = [
    "import hashlib,json,pathlib,sys,zipfile",
    "with zipfile.ZipFile(sys.argv[1]) as z:",
    " names=z.namelist()",
    " assert all(not n.startswith(('/', '\\\\')) and '..' not in pathlib.PurePosixPath(n).parts for n in names)",
    " data={n:json.loads(z.read(n).decode('utf-8')) for n in names if n.endswith('.json')}",
    " manifest=data['manifest.json']",
    " assert all(hashlib.sha256(z.read(f['path'])).hexdigest()==f['sha256'] for f in manifest['files'])",
    " pdfs={n:hashlib.sha256(z.read(n)).hexdigest() for n in names if n.endswith('.pdf')}",
    " print(json.dumps({'names':names,'data':data,'pdfs':pdfs},ensure_ascii=False))",
  ].join("\n");
  return JSON.parse(execFileSync(python, ["-B", "-c", script, file], { encoding: "utf-8", windowsHide: true, maxBuffer: 5 * 1024 * 1024 }));
}

test("真实账户生命周期：完整资料导出 ZIP 与原件校验、重验证注销及全部会话撤销", async ({ page, browser, pdf }) => {
  test.setTimeout(180_000);
  const account = await createAccount(page.request); await loginUI(page, account.email);
  const other = await browser.newContext({ baseURL: process.env.OFFERPILOT_E2E_WEB_ORIGIN || `http://127.0.0.1:${process.env.OFFERPILOT_E2E_WEB_PORT ?? 3001}` });
  const sibling = await browser.newContext({ baseURL: process.env.OFFERPILOT_E2E_WEB_ORIGIN || `http://127.0.0.1:${process.env.OFFERPILOT_E2E_WEB_PORT ?? 3001}` });
  try {
    const secondAccount = await createAccount(other.request);
    expect((await other.request.post("/api/v1/auth/login", { data: { email: secondAccount.email, password: syntheticPassword } })).status()).toBe(200);
    expect((await sibling.request.post("/api/v1/auth/login", { data: { email: account.email, password: syntheticPassword } })).status()).toBe(200);
    const otherBefore = await read(other.request, "/api/v1/me");
    const uploaded = await uploadUI(page, pdf, "合成账户生命周期.pdf");
    const headers = { "X-CSRF-Token": await csrf(page.request) };
    const saved = await page.request.put("/api/v1/profile", { headers, data: { revision: 0,
      education: [{ school: "合成大学", degree: "本科", major: "计算机科学", end_date: "2027-06", end_date_kind: "expected" }],
      skills: [{ name: "Python", level: "熟悉" }], projects: [{ name: "合成资料管理项目", role: "后端开发", description: "实现合成接口和单元测试" }],
      experiences: [{ organization: "合成科技", role: "开发实习生", experience_type: "internship", description: "合成经历用于本地验收" }],
    } });
    expect(saved.status()).toBe(200);
    await control(`resume/${uploaded.id}/candidates`);
    let profile = await read(page.request, "/api/v1/profile");
    const candidate = profile.candidates.find((item: { value: { name: string } }) => item.value.name === "Python");
    const reviewed = await page.request.post(`/api/v1/profile/candidates/${candidate.id}/review`, { headers, data: { revision: profile.revision, action: "confirm" } }); expect(reviewed.status()).toBe(200);
    profile = await reviewed.json();
    const requirement = "必须熟悉 Python。";
    const jobCreated = await page.request.post("/api/v1/jobs", { headers, data: { idempotency_key: randomUUID(), title: "后端实习生（合成生命周期）", company: "合成科技", location: "上海", jd_text: requirement, requirements: [{ type: "technical_skill", content: requirement, evidence_text: requirement, priority: "must", review_status: "confirmed" }] } });
    expect(jobCreated.status()).toBe(201); let job = await jobCreated.json();
    const marked = await page.request.patch(`/api/v1/jobs/${job.id}/favorite`, { headers, data: { revision: job.revision, is_favorite: true } }); expect(marked.status()).toBe(200); job = await marked.json();
    const matched = await page.request.post(`/api/v1/jobs/${job.id}/matches`, { headers, data: { idempotency_key: randomUUID(), profile_revision: profile.revision, job_revision: job.revision } }); expect(matched.status()).toBe(200); const match = await matched.json();
    const applied = await page.request.post("/api/v1/applications", { headers, data: { idempotency_key: randomUUID(), job_id: job.id, job_revision: job.revision, match_id: match.id } }); expect(applied.status()).toBe(201); let application = await applied.json();
    for (const actionStatus of ["pending", "completed"]) {
      const updated = await page.request.put(`/api/v1/applications/${application.id}`, { headers, data: { revision: application.revision, idempotency_key: randomUUID(), status: "submitted", submitted_at: null, notes: "本地合成投递备注", next_action: { title: "整理项目说明", notes: "已完成练习", due_date: null, due_time: null, status: actionStatus } } });
      expect(updated.status()).toBe(200); application = await updated.json();
    }
    await page.goto("/profile"); await openSettings(page); await screenshots(page, "account-settings");
    const record = await requestExport(page); await screenshots(page, "export-ready");
    await page.reload(); await expect(page.getByRole("button", { name: "下载 ZIP", exact: true })).toBeVisible();
    const downloadWait = page.waitForEvent("download"); await page.getByRole("button", { name: "下载 ZIP", exact: true }).click();
    const download = await downloadWait; const downloaded = path.join(output, "synthetic-user-export.zip"); await download.saveAs(downloaded);
    const zip = inspectZip(downloaded); const data = zip.data;
    expect(zip.names).toEqual(expect.arrayContaining(["manifest.json", "account.json", "profile.json", "resumes.json", "jobs.json", "matches.json", "applications.json", "profile_candidates.json", "application_events.json"]));
    expect(data["manifest.json"]).toMatchObject({ schema_version: "offerpilot-user-export/1.0", complete: true, timezone: "UTC" });
    expect(zip.pdfs[`resumes/${uploaded.id}.pdf`]).toBe(createHash("sha256").update(pdf).digest("hex"));
    expect(data["resumes.json"][0]).toMatchObject({ id: uploaded.id, original_file_status: "included" });
    expect(data["jobs.json"][0]).toMatchObject({ id: job.id, is_favorite: true });
    expect(data["matches.json"][0]).toMatchObject({ id: match.id, job_id: job.id });
    expect(data["applications.json"][0]).toMatchObject({ id: application.id, job_id: job.id, match_id: match.id, action_status: "completed" });
    expect(data["profile_entries.json"].some((entry: { evidence: unknown[] }) => entry.evidence.length > 0)).toBe(true);
    expect(data["profile_candidates.json"].length).toBe(3); expect(data["application_events.json"].length).toBeGreaterThan(2);
    const serialized = JSON.stringify(data);
    for (const secretField of ["password_hash", "token_hash", "status_token_hash", "object_key", "file_path", "key_reference", "authorization_id"]) expect(serialized.includes(`\"${secretField}\"`)).toBe(false);
    expect(serialized.includes(secondAccount.email)).toBe(false);
    expect((await other.request.get(`/api/v1/me/export/${record.id}`)).status()).toBe(404);
    expect((await other.request.get(`/api/v1/me/export/${record.id}/download`)).status()).toBe(404);
    await page.getByRole("button", { name: "查看注销说明并继续", exact: true }).click(); await screenshots(page, "deletion-confirmation");
    await page.getByRole("button", { name: "保留账户", exact: true }).click();
    const deletion = await confirmDeletion(page);
    await expect(page.getByRole("heading", { name: "账户注销已完成", exact: true })).toBeVisible();
    await screenshots(page, "deletion-completed"); await page.reload();
    await expect(page.getByRole("heading", { name: "账户注销已完成", exact: true })).toBeVisible();
    expect((await sibling.request.get("/api/v1/me")).status()).toBe(401);
    expect((await page.request.get("/api/v1/profile")).status()).toBe(401);
    expect((await page.request.get(`/api/v1/me/export/${record.id}/download`)).status()).toBe(401);
    expect((await sibling.request.post("/api/v1/auth/login", { data: { email: account.email, password: syntheticPassword } })).status()).toBe(401);
    expect(await read(other.request, "/api/v1/me")).toEqual(otherBefore);
    expect((await read(other.request, "/api/v1/resumes")).length).toBe(0);
    const inspected = await (await control(`resume/${uploaded.id}/inspect`)).json();
    expect(inspected).toMatchObject({ document_exists: false, text_exists: false, object_exists: false, claim_exists: false, partial_exists: false });
    const statusCookie = (await page.context().cookies()).find(cookie => cookie.name === "offerpilot_deletion_status");
    expect(Boolean(statusCookie?.httpOnly)).toBe(true); expect(statusCookie?.sameSite).toBe("Strict");
    await writeFile(path.join(output, "synthetic-account-lifecycle.json"), JSON.stringify({ export_id: record.id, deletion_id: deletion.id, zip_files: zip.names, original_pdf_sha256: zip.pdfs[`resumes/${uploaded.id}.pdf`], cross_user_isolation: true, all_sessions_revoked: true, deleted_files_verified: true, real_model_requests: 0 }, null, 2));
  } finally { await other.close(); await sibling.close(); }
});

test("真实导出过期拒绝下载，空账户可重新申请且注销密码错误不撤销会话", async ({ page }) => {
  const account = await createAccount(page.request); await loginUI(page, account.email); await openSettings(page);
  const record = await requestExport(page);
  await control(`export/${record.id}/expire`);
  expect((await page.request.get(`/api/v1/me/export/${record.id}/download`)).status()).toBe(410);
  await page.getByRole("button", { name: "刷新导出状态", exact: true }).click();
  await expect(page.getByText("文件已过期", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 ZIP", exact: true })).toHaveCount(0);
  await screenshots(page, "export-expired"); await requestExport(page);
  await page.getByRole("button", { name: "查看注销说明并继续", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByLabel("当前密码", { exact: true }).fill("Synthetic-wrong-password");
  await dialog.getByLabel("输入“注销账户”以确认", { exact: true }).fill("注销账户"); await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "确认注销并删除数据", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("密码");
  expect((await page.request.get("/api/v1/me")).status()).toBe(200);
  await dialog.getByRole("button", { name: "保留账户", exact: true }).click();
});

test("真实文件清理失败显示未完成、修复后恢复注销，丢响应仍可安全查询", async ({ page, pdf }) => {
  test.setTimeout(120_000);
  const account = await createAccount(page.request); await loginUI(page, account.email);
  const uploaded = await uploadUI(page, pdf); await openSettings(page);
  await control("lifecycle-dispatcher", { enabled: false });
  try {
    await control(`resume/${uploaded.id}/fault`, { mode: "delete" });
    let capturedId = "";
    await page.route("**/api/v1/me/deletion", async route => {
      const committed = await route.fetch(); expect(committed.status()).toBe(202);
      capturedId = (await committed.json()).id; await route.abort("failed");
    });
    await page.getByRole("button", { name: "查看注销说明并继续", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByLabel("当前密码", { exact: true }).fill(syntheticPassword);
    await dialog.getByLabel("输入“注销账户”以确认", { exact: true }).fill("注销账户"); await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "确认注销并删除数据", exact: true }).click();
    await expect(page).toHaveURL(/\/account-deletion$/); expect(capturedId.length).toBe(36);
    const failed = await control(`deletion/${capturedId}/process`); expect((await failed.json()).completed).toBe(false);
    await page.getByRole("button", { name: "刷新处理状态", exact: true }).click();
    await expect(page.getByRole("heading", { name: "数据清理尚未完成", exact: true })).toBeVisible();
    await screenshots(page, "deletion-cleanup-failed");
    expect((await page.request.get("/api/v1/me")).status()).toBe(401);
    await control(`resume/${uploaded.id}/fault`, { mode: "restore" });
    const recovered = await control(`deletion/${capturedId}/process`); expect((await recovered.json()).completed).toBe(true);
    await page.getByRole("button", { name: "刷新处理状态", exact: true }).click();
    await expect(page.getByRole("heading", { name: "账户注销已完成", exact: true })).toBeVisible();
    const inspected = await (await control(`resume/${uploaded.id}/inspect`)).json(); expect(inspected.object_exists).toBe(false);
  } finally {
    await control(`resume/${uploaded.id}/fault`, { mode: "restore" });
    await control("lifecycle-dispatcher", { enabled: true });
  }
});
