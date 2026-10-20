# S4-2 求职工作台与 MVP 闭环验收

开发：2026-10-15，最终收口：2026-10-16（Asia/Shanghai）。开始时工作区干净，分支 `codex/s4-1-application-tracker`，提交 `237272a`，迁移 head `0009_application_tracker`。本轮新建本地分支 `codex/s4-2-workbench`，保留原功能，没有远程推送、公开部署或修改既有开发/生产数据库。

**验收结论：MVP 功能闭环已完成本地验收。** 最终后端 **554/554**、前端 **125/125**、真实 Chromium **33/33** 通过，OpenAPI、TypeScript、ESLint、Production Build 和真实 HTTP 文件生命周期通过。没有未解决的本地核心 P0/P1；这不表示生产就绪或已公开上线，发布条件见第 7 节。本轮止于 S4-2，不自动进入 S5。

## 1. 产品变化与数据来源

工作台以行动为首要内容：有申请时先处理下一步，没有申请时先展示对应现有进度的建议入口。复用 S4-1 的单一当前行动；首页不是新的待办系统，也没有独立统计表、通知任务或模拟数据。主导航为工作台、职位、投递进度、我的资料；文件管理保留在资料与账户入口。

| 内容 | 权威数据及口径 |
|---|---|
| 逾期/今日/未来七天/未设日期 | 本人的 `applications` 当前行动；仅非终态且 pending，六组互斥；已完成/已取消另列，可展开恢复 |
| 准备投递 | 当前状态 preparing 的独立申请数 |
| 已投递 | 当前状态 submitted；不代表所有曾经投递的累计数，实际投递时刻仍可未知 |
| 笔试或面试 | 当前 assessment + interview |
| 收到 Offer | 当前 offer；不表示接受，也不视为结束 |
| 已结束 | 当前 rejected + withdrawn + closed |
| 总申请 | 以上五类之和；不统计收藏、匹配或历史事件次数，同一职位明确创建的不同申请轮次是独立记录 |
| 待处理职位 | `saved_jobs` + 当前结构条件 + 本人最新匹配元数据 + 是否存在本人的任何申请；去重后每岗位一项，可有多个原因 |
| 最近活动 | 既有 `application_events` 中创建、状态变化及完成行动的有限摘要；按系统记录时间排序，状态变化显示当次历史状态，不用当前状态覆盖旧事件含义 |
| 进度引导 | 本人 active 简历数、正式画像条目/非空偏好、职位和申请；待审核候选不当正式事实。已有业务进度优先，不强制回到上传步骤 |

待处理原因：收藏未建申请、JD 条件 pending/绑定旧 JD、无结构化条件、最新匹配依据失效、所有已有条件均为当前已确认且尚无匹配。下一步优先审核/补充条件，再看匹配，再决定是否创建申请。匹配失效复用 S3-2 的画像 revision、职位 revision/JD 版本和规则版本；收藏推进职位 revision 的保守失效行为保留。

**可推断范围：** 数据模型没有“JD 全文已完整审核”的完成标记。零条件不能证明已审核或未运行规则，因此明确显示“尚无结构化条件”，不能伪装为“已审核待匹配”。已有条件全部确认也只说明已保存条件的审核状态，不保证规则没有遗漏。当前行动被替换后，旧值只在原时间线保存，不额外重建可恢复任务列表。

## 2. 行动日期和状态

- 使用服务器返回的 `today`，固定北京时间 Asia/Shanghai（现代 UTC+08）。明确时刻规范化 UTC，显示北京时间；不依赖浏览器所在时区。
- 只有日期时保存民用日期，`due_time=null`；当日结束后才逾期，不补 00:00 或任何默认提醒时刻。
- 明确 HH:mm 时，在该分钟起点之后才逾期；恰好相等尚未逾期，下一微秒已逾期。今日组排除已逾期的定时行动。
- 未来七天为明日至第七天，两个日期边界均按北京时间。更晚的行动保留在投递列表；无日期进入独立组。
- 已完成/已取消从 pending 分组移出，保留日期和历史。恢复需要用户明确操作；终态申请须先重新开启。
- 申请进入 rejected/withdrawn/closed 时，服务端在同一 revision/幂等事务中将未完成行动取消，并追加变化事件。重新开启申请不会自动恢复行动。历史 S4-1 终态 pending 也不计入待办/逾期；无迁移或后台回填。
- 完成/取消/恢复仍走原申请 GET/PUT。首页先核对当前 revision 和行动，保留原备注、状态、投递时刻精度；未知结果重试复用原完整请求与幂等键，过期卡片不覆盖其他页面的修改。

## 3. API、查询及权限

新增 `GET /api/v1/dashboard?limit=5`，严格响应 `DashboardResponse`：`generated_at/timezone/today/actions/summary/jobs/recent_activity/onboarding`。limit 范围 1～20，分别限制六组行动、职位和最近活动；分组保留真实 total。无写入式 Dashboard API。现有列表增加 `next_action=pending|completed|cancelled|none` 与 `overdue=true|false`，保留其他参数及原申请 CRUD/事件契约。

前端类型由后端 OpenAPI 生成，代理只新增明确 GET 路径与不重复的 limit 参数；申请列表新筛选参数也在白名单中。不接受任意上游地址或可信客户端 user_id。写操作继续经过会话、CSRF、对象授权、revision、请求回执和事务。

工作台使用 SQL 聚合、条件 EXISTS、最新匹配元数据窗口与分组 ROW_NUMBER，每组读取有界数据；不将所有事件/JD/匹配 evaluation/简历正文拉进应用内存。活动只提取需要的历史状态，不返回 before/after 或备注。列表按页批量取职位/匹配/画像版本，消除逐行关联查询。正常工作台为 10 次领域读取，必要的引导回退为 11 次；有认证开销时见实测。列表最多 5 次领域读取，20/100 行不会成倍增加。

本轮没有新增依赖、索引、数据库字段或迁移。0001～0009 保持；不修改原模型提取或供应商配置。删除申请后其当前行动及活动自然消失；有申请时仍阻止删除职位，简历删除/匹配失效沿用原生命周期。

## 4. 完整 MVP 真实旅程

由 Playwright Chromium 经真实本机前后端执行，launcher 建立临时 SQLite 并迁移至 0009；只使用测试邀请、合成 PDF/JD 和本机私有文件目录，退出时清理。主旅程不拦截或模拟业务 HTTP，不调用 DeepSeek。

| 步骤 | 实际操作 | 结果 |
|---|---|---|
| 1 | UI 邀请注册，注册后独立登录 | 通过 |
| 2 | UI 上传合成文本 PDF，查看保存结果 | 通过 |
| 3 | 手工填写教育、技能并保存正式画像，来源为 manual | 通过；AI 候选为空 |
| 4 | UI 粘贴合成 JD 并填写最少岗位信息 | 通过 |
| 5 | 运行免费确定性整理，逐项审核并保存条件 | 通过；pending 候选经用户动作后才 confirmed |
| 6 | 执行确定性岗位匹配 | 通过；同页同步到申请面板 |
| 7 | 查看匹配结论和画像事实、JD 双方证据 | 通过；证据等于已存引文及原 JD 字符切片 |
| 8 | 创建准备投递并明确关联匹配（收藏独立另测） | 通过；关联 ID 与同页生成 ID 相同 |
| 9 | 记录已投递，未提供时刻仍为未知 | 通过；submitted_at=null |
| 10 | 添加仅日期下一步，不补具体时间 | 通过；due_time=null |
| 11 | 工作台查看今日行动和真实申请计数 | 通过；1 条已投递、1 条今日行动 |
| 12 | 从首页完成行动，核实移出待办且不逾期 | 通过；今日 0、已完成 1、is_overdue=false |
| 13 | 退出、重新登录、刷新，恢复已保存申请/行动 | 通过；原 PDF、画像、JD、匹配及申请仍在 |

额外浏览器场景覆盖六组真实数据、收藏不计入申请、搜索/行动/逾期/状态筛选、到期排序、13 条申请分页、详情返回筛选、键盘完成行动、取消确认、手机修改日期及会话失效。独立恢复场景使用真实并发编辑和服务端已提交后中断响应：旧卡片安全冲突，同一请求重试只追加一次事件。此故障注入与主旅程分开，不以 Mock 响应替代完整闭环。

## 5. 最终自动化结果

| 验证 | 实际结果 |
|---|---|
| 完整后端 unittest | **554/554 通过**，259.069 秒，exit_code=0，无失败/跳过；原 531 + 新 23 |
| S4-2 后端专项 | **23 项**纳入全量；分组、极端日期、统计、权限、删除失效、两个真实会话并发、回执重放和有界查询 |
| 完整前端 Vitest | **125/125 通过**，12 文件，12.70 秒，exit_code=0；原 99 + 新 26 |
| 完整真实 Chromium | **33/33 通过**，2.2 分钟，exit_code=0，无重试/跳过；原 29 + 新 4；修复后聚焦 2/2 亦通过（22.5 秒） |
| OpenAPI 契约 | 最终后端实际 Schema、保存 JSON、生成 TypeScript 一致，exit_code=0 |
| TypeScript / ESLint | 构建后 `tsc --noEmit` 通过；ESLint 0 错误/告警，两者 exit_code=0 |
| Production Build | 通过，所有路由生成；只本机构建，没有部署 |
| 真实 HTTP 文件生命周期 | 已通过，`exit_code=0` |
| 差异/迁移/依赖 | `git diff --check` 通过；0001～0009 和两端清单/锁文件无改动，无新增依赖 |

后端没有配置独立 formatter/typechecker，不虚报执行。完整套件涵盖原 S1～S4-1、迁移、AI Schema、身份与文件生命周期；真实 Provider 相关回归均使用本地故障/协议桩，不能代表模型实际质量。没有将跳过或未执行项写为通过。

运行记录透明保留：Chromium 初轮 31/33，两个失败为证据引文标点断言及 Next.js 路由播报 alert 定位冲突；按实际已存引文/原文切片和主内容告警修正后，聚焦发现真正的同页匹配同步缺口（1/2）。修复三个组件并新增 4 条回归，聚焦 2/2、完整 33/33 最终通过；没有用刷新页面绕过失败。构建初轮发现新增 Testing Library 测试误用 Playwright 的 `exact` 选项，去掉不支持的参数后构建和独立 TypeScript 通过，不放宽默认字符串精确名称断言。

日志在 `frontend/artifacts/s4-2/`：`backend-final.log`、`frontend-final.log`、`chromium-full-final.log`、`chromium-focused-final.log`、`openapi-final.log`、`eslint-final.log`、`build-final.log`、`typescript-final.log`、`http-lifecycle.log`、`diff-final.log`，最终记录均有 `exit_code=0`。初轮失败日志另留，不能当作最终通过证据。测试结束后本机隔离 3001/8001 无监听，构建与 E2E 顺序执行。

本机规模探测：临时 SQLite 中 **1,000 条申请 + 30,000 条事件**，真实认证 TestClient 连续 10 次、每组 limit=5；总数保持 1,000、返回均有界，含认证每次 **12 条 SQL**。耗时 45.94～149.10 ms，中位数 **48.215 ms**（含首轮冷启动，不足以推断生产 P95）。报告 `frontend/artifacts/s4-2/performance.json`；这不代替 PostgreSQL、网络服务或正式容量测试。

## 6. 界面证据与使用体验走查

39 张实际 PNG 均由最终真实 Chromium 运行重新生成，只含合成资料，保存在 Git 忽略的 `frontend/artifacts/s4-2/`，可重复执行 E2E 重生。1440/768/390px 的工作台、行动、筛选和匹配页面检查无横向溢出；实际查看了新用户、今日、逾期、完成、列表、申请及匹配证据的桌面/平板/手机代表截图。手机行动按钮/链接最小点击高度 44px，键盘完成、确认弹窗及 Escape 取消由真实浏览器场景覆盖；不宣称实体手机、其他浏览器、完整 WCAG 或屏幕阅读器认证。

| 场景 | 实际截图 |
|---|---|
| 新用户起点 | [桌面空工作台](../frontend/artifacts/s4-2/dashboard-empty-1440-viewport.png)、[手机空工作台](../frontend/artifacts/s4-2/dashboard-empty-390-viewport.png) |
| 今日行动与进展 | [桌面](../frontend/artifacts/s4-2/dashboard-today-1440.png)、[平板](../frontend/artifacts/s4-2/dashboard-today-768.png)、[手机](../frontend/artifacts/s4-2/dashboard-today-390-viewport.png) |
| 逾期与筛选 | [桌面逾期](../frontend/artifacts/s4-2/dashboard-overdue-1440.png)、[手机逾期](../frontend/artifacts/s4-2/dashboard-overdue-390-viewport.png)、[筛选列表](../frontend/artifacts/s4-2/applications-filtered-1440-viewport.png)、[手机筛选](../frontend/artifacts/s4-2/applications-filtered-390.png) |
| 完成与持续管理 | [手机完成成功反馈](../frontend/artifacts/s4-2/dashboard-completed-390.png)、[手机工作台](../frontend/artifacts/s4-2/dashboard-mobile-390.png) |
| 上传/正式资料/审核 | [PDF 已保存](../frontend/artifacts/s4-2/mvp-resume-1440.png)、[手工正式资料](../frontend/artifacts/s4-2/mvp-profile-manual-1440.png)、[JD 人工审核](../frontend/artifacts/s4-2/mvp-jd-reviewed-1440.png) |
| 双方匹配证据 | [桌面](../frontend/artifacts/s4-2/mvp-match-evidence-1440.png)、[手机证据](../frontend/artifacts/s4-2/mvp-match-evidence-390-viewport.png) |
| 申请关联与保存 | [桌面申请](../frontend/artifacts/s4-2/mvp-application-1440-viewport.png)、[手机申请](../frontend/artifacts/s4-2/mvp-application-390.png) |

JSON 证据：[完整合成旅程](../frontend/artifacts/s4-2/synthetic-mvp-journey.json)、[真实行动分组](../frontend/artifacts/s4-2/synthetic-workbench-groups.json)、[规模探测](../frontend/artifacts/s4-2/performance.json)。这些仅是合成验证结果，不是产品内置数据。

本次是工程师按首次使用大学生的任务进行检查，**未招募外部用户，不是实际用户可用性试点**。

| 用户视角检查 | 本次证据与判断 |
|---|---|
| 不读开发文档完成流程 | 工程师可按可见中文入口完成 13 步；普通学生独立完成率仍待试点 |
| 收藏与投递区别 | 首页/列表/职位面板有明确说明，收藏不改变申请计数，创建计划后才出现申请 |
| 未发现证据含义 | 匹配页明确写“未发现证据不等于不具备能力”；Java 未知项与 Python 明确满足并列 |
| 哪些 AI 信息需确认 | 规则整理初始 pending，须逐项确认后保存；正式手工资料显示用户填写，旧 Mock/AI 候选审核回归保留 |
| 维护申请状态 | 详情可直接选状态、保存、看时间线、完成/取消/恢复行动；不猜投递时刻或接受 Offer |
| 首页找到下一步 | 有数据先列逾期/今日；新用户先列可跳过的引导；点击行动直接进入对应记录 |
| 手机主要操作 | 390px 测试完成、取消/恢复、筛选及修改日期；没有页面横向溢出，未验证实体触屏设备 |
| 重复输入及点击 | 创建申请复用岗位基本信息，同页匹配后可直接关联；无 Key 时手工建立正式画像仍需用户明确输入事实 |
| 成功/统计/匹配是否误导 | 等后端成功才反馈，当前状态去重计数；零条件、历史失效、未知时刻/能力均明确标记 |
| 错误恢复 | 数据读取可重试，旧卡片/409 要求刷新核对，未知写入保留同键，登录失效返回登录；真实故障场景通过 |

| 等级 | 发现及处理 |
|---|---|
| P0 | 未发现未解决的本地核心 P0；发布条件另列，未冒充完成 |
| P1 已修复 | 首页仍是文件管理，无法快速知道下一步；替换为行动优先、真实申请和职位入口 |
| P1 已修复 | 新用户四组空待办挤占首屏；空组折叠，已有进度的主要操作优先展示，不重复要求上传 |
| P1 已修复 | 结束申请遗留 pending；服务端同事务取消，历史终态也不提示逾期 |
| P1 已修复 | 首页旧卡片/未知响应可能覆盖新行动或重复操作；核对 revision/行动、保留请求键、冲突刷新与成功反馈 |
| P1 已修复 | 申请详情返回丢失筛选；URL 保存搜索、状态、行动、逾期、排序、页码，安全限定返回列表路径 |
| P1 已修复 | 空偏好对象误判为已就绪；验证非空正式值，候选不计入。零条件不假称已审核，历史活动不误用当前状态 |
| P1 已修复 | 同页计算匹配后申请面板仍使用初始空结果，必须刷新才能关联；成功回调驱动申请面板重新读取，并绑定明确的匹配 ID，完整旅程保留不刷新断言；新增 4 条跨面板/历史/勾选/换账户回归 |
| P2 后续 | 待办每组默认只显示 5 条，其余通过投递列表查看；尚无完整日历或多行动计划，按真实反馈再决定 |
| P2 后续 | 验证真实设备/屏幕阅读器及长期实际数据分布；单次工程走查不能证明普通学生能独立完成或理解全部术语 |
| P2 后续 | 匹配证据同时展示条目 ID、规则版本和字符位置；追溯可用，但普通学生阅读时可进一步折叠技术元信息，保留事实与 JD 原文为主 |

收藏/投递区别、未发现证据不等于没有能力、候选须确认、未知信息可留空、用户自行提交申请和 Offer 不等于接受，均在对应流程保留可见说明。创建申请复用职位名称/公司/地点，手工资料可部分保存；不要求重复录入已有事实或先运行模型。

## 7. 文档差异与发布条件

实现前核实 README 的“当前边界”仍写“没有投递管理/止于 S3-2”，与 `237272a` 实际 S4-1 不符；本轮纠正当前说明并保留历史验收数字。PLAN 早期三个阶段问题属 S2 审查，现明确标为历史。S4-1 的“结束可保留 pending”实际服务缺口本轮改为服务端取消，并同步 PRD/架构/数据库；用户明确授权 S4-2 覆盖旧文档停止点，其他边界继续保留。

仍未完成的发布条件：

1. PostgreSQL 兼容、向前迁移与并发验证。
2. 生产部署、容量、HTTPS、私有文件权限/解析隔离与运行维护验证。
3. 用户数据导出、账户注销，覆盖原件、派生、匹配、申请/历史/回执、缓存及相关任务。
4. 安全、隐私、许可及第三方模型 BYOK/保留/训练配置核查；单样本真实结果不代表总体质量。
5. 真实用户试点及普通学生独立操作的证据。
6. 生产备份、恢复、删除重放、保留期限及监控。

S5 仅建议在后续明确授权后，先建立隔离预发布环境、完成上述发布条件，再小范围招募试点；本轮不执行 S5，不推送 GitHub、不公网部署。

## 8. 可重复验证

```powershell
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///:memory:'
$env:OFFERPILOT_ENVIRONMENT = 'development'
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
& .\backend\.venv\Scripts\python.exe -B tests/http_file_lifecycle_smoke.py
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run test:e2e
npm.cmd --prefix frontend run api:check
npm.cmd --prefix frontend run lint
# E2E 与 build 顺序执行；build 后再做 TypeScript，避免共用 .next 竞争。
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run typecheck
git diff --check
```

## 9. 实际文件

共 39 个源码/测试/契约/文档文件，无依赖或迁移改动；截图和运行日志另存忽略目录。

| 文件 | 变更目的 |
|---|---|
| `backend/src/offerpilot_api/api/routes/dashboard.py`（新）、`api/routes/applications.py`、`main.py` | 认证汇总路由、列表筛选参数、装配和显式错误契约 |
| `backend/src/offerpilot_api/application_tracker/dashboard.py`（新）、`schemas.py`、`services.py` | 轻量投影、严格响应、行动/日期判定、终态取消、批量关联查询 |
| `tests/test_dashboard.py`（新） | 23 项真实 API/DB、时间、安全、并发、幂等与性能边界回归 |
| `frontend/src/components/dashboard.tsx`、`workspace-shell.tsx`、`profile-page.tsx` | 行动首页、四入口导航、资料和账户保留简历管理 |
| `frontend/src/components/applications-page.tsx`、`application-page.tsx`、`frontend/src/app/applications/page.tsx`、`frontend/src/app/applications/[id]/page.tsx` | 行动/逾期筛选、URL 与返回列表、加载和终态说明 |
| `frontend/src/components/job-page.tsx`、`job-match.tsx`、`job-applications.tsx` | 匹配完成后同页同步、关联 ID 选择与用户/职位范围保护 |
| `frontend/src/components/dashboard.test.tsx`（新）、`job-page-match.test.tsx`（新）、`applications.test.tsx`、`profile.test.tsx` | 首页状态/恢复/冲突、真实组件协作、列表筛选与已有未保存保护 |
| `frontend/src/app/globals.css` | 复用 Token 的工作台、四入口和响应式布局；手机行动控件点击区域至少 44px |
| `frontend/src/lib/api.ts`、`api.test.ts`、`proxy.ts`、`proxy.test.ts` | 生成类型接入、只读白名单及新筛选参数/重复参数边界 |
| `frontend/openapi.json`、`frontend/src/lib/schema.d.ts` | 从实际后端导出/生成的共享契约 |
| `frontend/e2e/workbench.spec.ts`（新）、`profile.spec.ts`、`workspace.spec.ts` | 4 个新真实 E2E、截图与原导航/空状态断言适配；旧能力覆盖保留 |
| `README.md`、`docs/PLAN.md`、`PRD.md`、`architecture.md`、`database.md`、`roadmap.md`、`S4-2-acceptance.md`（新）、`frontend/DESIGN.md` | 同步当前行为、数据口径、历史差异、验收与停止点 |
