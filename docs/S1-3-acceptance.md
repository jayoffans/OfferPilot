# S1-3 中文 Web 工作台验收记录

日期：2026-10-11。仓库：`D:\agent 项目\OfferPilot`；分支：`feature/resume-agent`；开发前 HEAD：`9ec576d06867a3d44eeaa255e03302f80d9faf8e`。保持当前分支和原有未提交内容，没有创建提交或推送远程。

**结论：S1-3 达到本机开发验收标准。** 用户可通过真实中文页面完成邀请注册、登录、工作台引导、PDF 上传/查询/改名/原件下载/删除及失败恢复、退出。业务连接现有 FastAPI，正式页面没有演示数据。文件保存、文本解析和尚未开放的 AI 画像分别说明。完整 MVP 与生产上线条件仍未完成。

## A. 代码、架构与契约

开发前读取 PRD、architecture、database、roadmap、PLAN、README 和项目规则；核查真实路由、会话/CSRF、元数据与生命周期响应、Git 未提交内容和迁移。先重跑后端基线并导出实际 OpenAPI，再实现 Web。工作区中既有 S1-1/S1-2 改动属于前序工作，本批没有重写后端、修改后端依赖或新增数据库迁移。

| 页面 | 已实现行为 |
|---|---|
| `/register` | 邮箱绑定邀请、12～128 字符密码、字段错误、密码显隐、提交状态；真实 201 后转登录 |
| `/login` | 登录、密码显隐、安全错误、限流反馈、提交防重、会话恢复；已登录访问认证页转工作台 |
| `/` | 首次上传引导、空状态、真实文件计数/最近上传时间、最近简历、下一步；明确 AI 尚未开放 |
| `/resumes` | PDF 上传、搜索、排序、每页 8 份、空结果/空账户、加载/失败/重试、刷新恢复 |
| `/resumes/[id]` | 文件详情、完整文件名、改名、原件下载、删除确认、清理状态、删除失败重试及完成反馈 |
| 账户菜单 | 当前邮箱与真实服务端退出；无尚未实现的空功能入口 |

App Router 管路由，组件管交互，`src/lib` 统一 API、认证和同源代理。FastAPI 保持唯一身份、授权和领域权威，前端不访问数据库。采用 Next.js 16.4.0、React 19.3.0、严格 TypeScript 5.9.3、Tailwind 4.3.3、Radix Dialog/Dropdown、Lucide 及 OpenAPI TypeScript。直接依赖固定版本，传递版本锁在 package-lock.json；用途、维护和成本见 [设计约定](../frontend/DESIGN.md)。

### 实际文件清单

以下为本批新建前端文件和修改的仓库文档；其他 backend/tests 脏文件为保留的前序内容。

| 范围 | 仓库相对路径 |
|---|---|
| 工程和运行 | `frontend/package.json`、`package-lock.json`、`.env.example`、`AGENTS.md`、`tsconfig.json`、`next-env.d.ts`、`next.config.ts`、`postcss.config.mjs`、`eslint.config.mjs`、`vitest.config.ts`、`playwright.config.ts` |
| 真实 API 契约与生成 | `frontend/openapi.json`、`src/lib/schema.d.ts`、`scripts/export-openapi.py`、`scripts/check-contract.mjs` |
| API/会话/代理 | `frontend/src/lib/api.ts`、`auth.tsx`、`proxy.ts`；`frontend/src/app/api/v1/[...path]/route.ts` |
| 路由与全局 UI | `frontend/src/app/layout.tsx`、`globals.css`、`page.tsx`、`login/page.tsx`、`register/page.tsx`、`resumes/page.tsx`、`resumes/[id]/page.tsx`、`error.tsx`、`not-found.tsx` |
| 产品组件 | `frontend/src/components/auth-screen.tsx`、`workspace-shell.tsx`、`dashboard.tsx`、`resumes-page.tsx`、`resume-detail.tsx`、`upload-dialog.tsx`、`ui.tsx`、`use-resume-list.ts` |
| 单元/交互测试 | `frontend/src/lib/api.test.ts`、`proxy.test.ts`、`src/components/interactions.test.tsx`、`src/test/setup.ts` |
| 真实 E2E 与隔离服务 | `frontend/e2e/workspace.spec.ts`、`fixtures.ts`、`global-setup.ts`；`frontend/scripts/e2e-backend.py` |
| 设计与交付文档 | `frontend/DESIGN.md`、`README.md`、`docs/PLAN.md`、`docs/PRD.md`、`docs/architecture.md`、`docs/database.md`、`docs/roadmap.md`、本文 |
| 忽略规则 | `.gitignore` 增加依赖、构建、覆盖率、浏览器报告与截图目录；保留既有规则和 `.gitkeep` |

### API 集成与安全边界

- 使用真实 `auth/register`、`auth/login`、`auth/logout`、`auth/csrf`、`me` 和全部七个文件管理 API。四字段元数据与生命周期单独处理，不补造文件大小、AI 事实或任务进度。
- `openapi.json` 由当前 FastAPI 导出，`schema.d.ts` 生成；`api:check` 检查当前后端 Schema 与生成类型是否漂移。导出不查询个人记录，不执行迁移。
- 浏览器只访问同源 `/api/v1`。Next 代理白名单限定已使用的方法/路由与 UUID，不转发任意 URL、query、profile 或测试控制端点；后端地址和 Web Origin 白名单仅通过服务端环境变量配置。
- 仅转发必要头和会话 Cookie，保留 Origin/CSRF，安全处理 Set-Cookie/生命周期 Location，禁止缓存与上游重定向。请求体上限 11 MiB（含 multipart 开销），代理超时 55 秒，客户端 60 秒，取消和读取有界。
- 会话来自 HttpOnly、SameSite=Lax Cookie；localStorage/sessionStorage 不存凭据。CSRF 只在内存中，刷新后读取真实接口；旧响应不能覆盖新登录状态。
- 401 返回登录；404 统一不可访问提示；409 提示冲突/完整性核查；429 不自动重试；503 清理失败保留状态并允许重试。后端执行授权，不信任客户端 owner。
- 上传百分比来自真实 XHR 传输事件；100% 仅表示传输完成，须等 201 确认保存/文本解析。网络中断、超时或无法验证的响应标记结果未知，阻止盲目重复上传，提供列表核查或生命周期链接。
- 页面离开取消请求/轮询，处理状态最多自动查询 10 次、间隔 2 秒。文件名以 React 文本渲染，下载后撤销 blob URL。密码、Token、Cookie 和正文不写入应用日志或截图。

## B. Windows 运行与安全账户流程

完整的两个终端命令、邀请码 CLI、环境变量和停止方式见 [README 本地启动](../README.md#本地启动windows-powershell两个终端)。使用新本机 SQLite、独立私有目录、`development`，迁移到 `0003_resume_file_lifecycle`。不要替换为生产数据库。

需要 Python 3.11、uv、兼容的 Node；本次实际 Node 22.16/npm 10.9。后端 localhost:8000、前端 localhost:3000，双方 Origin 配置一致，前端只绑定环回。测试账户由本机邀请 CLI 用自己指定的邮箱生成一次性邀请码，在页面设置自己的密码；报告不交付真实凭据。

可复现验证入口：

```powershell
Set-Location 'D:\agent 项目\OfferPilot\frontend'
npm.cmd ci
npm.cmd run api:check
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test
npm.cmd run build
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

后端和真实 HTTP 命令见 [README 隔离回归](../README.md#隔离回归)。Playwright 自动生成合成账户/邀请/PDF，凭据只保留在内存；使用临时数据库/私有目录，默认 8001/3001，拒绝复用占用端口。退出关闭持有的进程、数据库并删除自己的临时根目录，清理失败使测试失败。

## C. 实际验收结果

以下为最终有效结果；调试中失败已修复，未计为跳过或最终通过。

| 检查 | 通过 | 失败 | 跳过 | 结果与范围 |
|---|---:|---:|---:|---|
| 当前后端 unittest 全量 | 125 | 0 | 0 | 开发前实际重跑约 34.8 秒；本批后续未改后端 |
| 独立后端真实 HTTP | 1 次运行 | 0 | 0 | `http_file_lifecycle_smoke.py` exit 0；隔离迁移、两账号、实际下载/清理 |
| npm 锁文件安装 | 1 次 `npm ci` | 0 | 0 | 实际成功；安装告警另记 |
| OpenAPI 契约检查 | 通过 | 0 | 0 | 当前后端、导出 Schema 和生成类型一致 |
| TypeScript | 通过 | 0 | 0 | 严格模式 `tsc --noEmit` |
| ESLint | 通过 | 0 | 0 | lint 输出无 error/warning；安装 peer 告警另记 |
| Next.js 生产构建 | 通过 | 0 | 0 | 构建及路由生成完成；没有部署 |
| Vitest | 16 | 0 | 0 | 3 个文件：API/会话 4、代理 6、认证/上传交互 6 |
| Playwright 全套 Chromium | 7 | 0 | 0 | 真实 Next + FastAPI + 临时 SQLite/私有文件；约 1 分钟 |
| 截图更新后的定向复验 | 2 场各 1 | 0 | 0 | 响应式约 17.8 秒；完整文件闭环约 17.3 秒 |
| 文档/差异检查 | 通过 | 0 | 0 | 链接、截图清单及 `git diff --check`；无远程写入 |

### 七个真实浏览器场景

1. 注册 → 登录 → 上传 → 详情 → 刷新 → 退出重登录 → 改名 → 下载 → 删除 → 退出。下载 PDF 与上传字节完全一致，名称一致。删除前插入合成派生记录，删除后核查 PDF/part/claim、正文、文档/Profile/Fact 行均不存在，最小删除记录无对象引用；重复 DELETE 为 204。
2. 两个独立账户和匿名 BrowserContext：B 列表不含 A 文件；B 访问 A 详情/下载/生命周期/改名/删除为 404，匿名 401；未知与他人资源响应相同。缺少 CSRF、不可信 Origin 写入 403。Cookie HttpOnly/SameSite，浏览器存储为空。
3. 实际文件系统故障使 DELETE 返回 503，页面保留删除失败及恢复入口；刷新读持久状态，解除故障后重试实际删除完成。
4. 改动原件字节导致真实下载 409，页面提示完整性问题；非 PDF 内容上传失败，不增加简历或显示成功。
5. 真实错误登录触发 429，页面提示稍后重试；观测只有一次 UI 请求，无自动重试。
6. 实际停启测试后端，页面反馈服务不可用，恢复后文件仍可见；服务端会话失效后重载转登录，`me` 为 401。
7. 真实上传 12 份 PDF，验证搜索/排序及 8/4 分页；危险文件名仅显示文字，无注入 img 节点。三种宽度无主要横向溢出；上传/改名弹窗 Escape 关闭并恢复按钮焦点。

主闭环未 Mock API。控制端点仅附加到隔离测试 app，要求环回与随机秘密；正式 app/OpenAPI、Web 代理均不开放。合成派生记录只用于清理验证，不运行 AI。

单元测试另覆盖网络/未知上传结果、提交防重、安全错误映射、旧 CSRF/401 不能破坏新会话、非法代理路径/Origin、请求体上限/中止等边界。超时和竞态的部分边界由这些测试覆盖，不冒称全部经过真实断网实测。

### 真实页面和截图

已在 Chromium 驱动本机页面完成闭环，也在 Codex 内置浏览器实际打开登录页。截图来自运行中的页面，仅含合成邮箱/文件名，无密码、邀请、Cookie 或正文。人工查看 PNG 检查导航、字号、间距、空状态、12 份文件、长/特殊文件名和弹窗；修复移动溢出、平板账户区换行及弹窗焦点恢复后复验。

| 页面/状态 | 1440px | 768px | 390px |
|---|---|---|---|
| 登录 | [截图](../frontend/artifacts/screenshots/login-1440.png) | [截图](../frontend/artifacts/screenshots/login-768.png) | [截图](../frontend/artifacts/screenshots/login-390.png) |
| 邀请注册 | [截图](../frontend/artifacts/screenshots/register-1440.png) | [截图](../frontend/artifacts/screenshots/register-768.png) | [截图](../frontend/artifacts/screenshots/register-390.png) |
| 有文件工作台 | [截图](../frontend/artifacts/screenshots/dashboard-1440.png) | [截图](../frontend/artifacts/screenshots/dashboard-768.png) | [截图](../frontend/artifacts/screenshots/dashboard-390.png) |
| 12 份文件列表 | [截图](../frontend/artifacts/screenshots/resumes-1440.png) | [截图](../frontend/artifacts/screenshots/resumes-768.png) | [截图](../frontend/artifacts/screenshots/resumes-390.png) |
| 文件详情 | [截图](../frontend/artifacts/screenshots/resume-detail-1440.png) | [截图](../frontend/artifacts/screenshots/resume-detail-768.png) | [截图](../frontend/artifacts/screenshots/resume-detail-390.png) |
| 空工作台 | [截图](../frontend/artifacts/screenshots/dashboard-empty-1440.png) | — | [截图](../frontend/artifacts/screenshots/dashboard-empty-390.png) |
| 空列表 | [截图](../frontend/artifacts/screenshots/resumes-empty-1440.png) | — | [截图](../frontend/artifacts/screenshots/resumes-empty-390.png) |
| 上传弹窗 | [截图](../frontend/artifacts/screenshots/upload-dialog-1440.png) | — | [截图](../frontend/artifacts/screenshots/upload-dialog-390.png) |

另有桌面 [改名弹窗](../frontend/artifacts/screenshots/rename-dialog-1440.png)、[删除确认](../frontend/artifacts/screenshots/delete-confirm-1440.png)、[删除完成](../frontend/artifacts/screenshots/deleted-1440.png)，共 24 张。所有主要页面三种宽度均被自动检查；空状态和上传弹窗另补桌面/手机截图。语义弹窗、表单标签、错误提示、可见焦点及关键焦点回归已验证，未宣称完整 WCAG 审计。

截图被 Git 忽略，属于本机验收材料。复现全部 24 张需运行全套 E2E；响应式场景生成 21 张，首个闭环生成其余三张。full-page 弹窗图可能包含视口以下区域，遮罩只覆盖真实视口，人工检查以可见视口为准。

### 技术债和未执行范围

- Next ESLint 传递的 import/react/jsx-a11y 插件 peer 范围仍只声明至 ESLint 9；ESLint 10 安装有告警，完整 lint 实际通过。保留维护中的 ESLint 10，等待上游声明更新；jsdom 传递的 whatwg-encoding 也有弃用提示。未声称完成依赖漏洞扫描。
- 后端已有 httpx 测试兼容弃用提示；本批不升级或重构后端。SQLite 锁内同步解析/IO、Windows 目录 ACL、删除记录保留及备份清理等 S1-2 限制继续保留。
- 只验证本机 Chromium 与指定 viewport；未执行 Firefox、WebKit、实体手机、正式用户研究、屏幕阅读器或完整无障碍认证。
- 未连接 PostgreSQL/生产库，未验证 HTTPS/公网代理、容量/压力、灾备、备份删除重放、生产解析隔离/超时、邮件或云存储。上传仍限 development。
- 账户导出/注销、密码找回/修改、邮箱验证、AI 画像/BYOK、岗位匹配、投递管理和 Browser Agent 未实现，界面没有伪装这些能力。

## D. 文档、清理与停止边界

README 提供 Windows 命令和安全邀请流程；DESIGN 记录结构/Token/交互/依赖；PRD/architecture/database/roadmap 同步实际范围；PLAN 保留原基线和前批历史，新增 S1-3 记录。

验收后只读核查未发现本批持有的 Next 或 E2E Python 服务，`offerpilot-web-e2e-*` 临时根目录已清理。其他任务临时目录未删除；依赖、构建产物和截图留作本机复查并被忽略。当前没有持续运行的预览服务，启动方式见 README。

本轮没有付费模型请求、真实简历外传、生产库连接、公网部署、Git 提交或远程推送。**S1-3 完成后停止，不进入 S2。**
