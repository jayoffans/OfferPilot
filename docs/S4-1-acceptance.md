# S4-1 真实求职申请管理验收

日期：2026-10-15（Asia/Shanghai）。起始分支 `codex/s3-1-job-management`；全部已验收但未提交的 S3-2 内容保存为本地检查点 `290a988`，随后创建 `codex/s4-1-application-tracker`。没有覆盖原改动、推送远程、迁移实际开发/生产数据库或公网部署。

**验收结论：S4-1 达到本机功能验收标准。** 最终后端 **531/531**、前端 **99/99**、真实 Chromium **29/29** 通过；生成契约、TypeScript、ESLint、生产构建及 HTTP 文件生命周期均通过。本轮只交付 S4-1；S4-2 聚合工作台、Browser Agent、通知派发、真实 DeepSeek 调用均未开发或执行。

## 1. 交付的真实用户流程

登录 → 打开已保存职位 → 可查看岗位匹配 → 明确点击“准备投递” → 创建独立申请 → 手工修改状态/实际投递时间 → 备注 → 下一步行动 → 申请时间线 → 刷新与重新登录恢复 → 持续修改或结束。

申请创建复用岗位名称、公司、城市；没有匹配结果、画像不完整或存在硬条件冲突也允许用户自行建立记录。收藏仍只是关注，不创建申请、不生成实际投递时间。收到 Offer 只记录收到，不替用户接受、签署或决定。

## 2. 修改文件及架构

| 文件/模块 | 职责 |
|---|---|
| `backend/src/offerpilot_api/application_tracker/{schemas,services}.py` | 严格申请/行动契约、owner 过滤、事务、revision、幂等、重复申请策略、时间规范、追加式历史 |
| `backend/src/offerpilot_api/api/routes/applications.py` | 认证 API 装配；领域规则留在服务层 |
| `backend/src/offerpilot_api/db/models.py`、`main.py` | 三个模型、路由、安全错误和字段白名单；复用原身份/CSRF |
| `backend/src/offerpilot_api/job_catalog/services.py` | 有关联申请的职位删除返回明确 409，原事务回滚不改 revision |
| `backend/migrations/versions/0009_application_tracker.py` | 0008 后单向新增结构，历史 0001～0008 不改 |
| `frontend/src/components/{applications-page,application-page,applications-ui,job-applications}.tsx` | 中文列表/详情/状态表单/行动/时间线及职位创建入口 |
| `frontend/src/app/applications/**/page.tsx`、`components/{workspace-shell,job-page,jobs-page}.tsx`、`app/globals.css` | 路由、第五个导航、替换历史未开发说明、响应式样式 |
| `frontend/src/lib/{api,proxy}.ts`、`openapi.json`、`schema.d.ts` | 真实 Schema 生成类型、客户端、严格代理白名单，不手写漂移契约 |
| `tests/test_applications.py`、`test_application_migration.py`、`test_job_match_migration.py` | API/DB/并发/日期/生命周期/迁移；原 0008 测试仅适配新 head |
| `frontend/src/components/applications.test.tsx`、既有 jobs/api/proxy 测试 | 新 UI、安全/网络边界与原回归 |
| `frontend/e2e/applications.spec.ts`、`scripts/e2e-backend.py` | 真实 Chromium、隔离临时 0009 库、合成数据和截图 |
| README、PLAN、PRD、architecture、database、roadmap、frontend/DESIGN、本文件 | 当前能力、规则/数据生命周期、验证和停止点 |

没有新增依赖、大型 Agent 框架、队列、调度器或跨模块重构。UI 不访问数据库，画像和匹配模块不接收申请输入作为正式事实。

## 3. 模型、迁移和数据生命周期

`applications` 独立保存 owner、职位 FK、创建时最小岗位标签、轮次、状态、实际投递 UTC 时刻（可未知）、备注、单一当前行动、revision、创建/更新时间。当前行动使用内容、备注、民用日期、可选分钟时间及状态列；不需要另建行动表或提醒系统。修改前后值由事件保存，替换行动不会丢掉此前安排。

`application_events` 保存 owner、申请、资源 revision、事件类型、before/after、业务发生时刻与系统记录时刻。`application_receipts` 保存 owner 唯一键、请求摘要、操作、可空申请引用和时间；不含请求正文、备注或行动副本。

0009 新增三表、外键、唯一及 CHECK 约束：同用户/职位/轮次唯一，非终态 `active_slot=1` 唯一；终态 slot 为 NULL，允许多轮历史。空库、含 0008 各表真实合成记录的逐表快照、ORM 元数据、外键、约束、空结构降级均有测试；存在任何新数据或墓碑时拒绝有损降级，offline 也不能绕过。

数据用途仅为本人长期求职跟踪，不收集新的联系方式、工作授权或完整简历。默认保留至本人明确删除，不擅自按天清历史；备注可能含用户主动输入的个人信息，不进入生产日志/异常/模型。普通清空或更正备注仍在时间线保留前值，删除整条申请才一并清除其备注、行动、事件。

删除申请清事件/当前内容及 update 回执；create/delete 的摘要墓碑保留，旧创建请求返回 409，不能复活记录；同一删除重试仍 204。存在任何申请（含终态）时阻止删除职位，先确认删除相关申请，再删职位。数据库 RESTRICT 是第二道防线；创建与职位删除在职位版本围栏串行。

申请只引用匹配 ID，没有简历版本引用或简历/画像副本。删除简历仍由既有事务清候选和匹配来源引文，用户确认值遵循 S2 规则，申请状态/备注/历史不变。画像、职位/JD、规则变化后关联匹配动态显示历史失效；不把旧结论当当前建议，不自动推进申请。未来匹配物理删除时 FK SET NULL，申请保留。

边界：完整账户导出/注销尚未交付，必须纳入三表及墓碑、备份和恢复后删除重放；备份保留期限及 PostgreSQL/生产数据库仍待独立验收。SQLite 逻辑删除不代表磁盘安全擦除。

## 4. 状态、时间线和时间规范

| 状态 | 中文 | 是否结束当前一轮 |
|---|---|---|
| preparing | 准备投递 | 否 |
| submitted | 已投递 | 否；可展示等待回复含义 |
| assessment | 笔试 | 否 |
| interview | 面试 | 否 |
| offer | 收到 Offer | 否；不表示接受 |
| rejected | 被拒绝 | 是 |
| withdrawn | 主动撤回 | 是 |
| closed | 已结束 | 是 |

没有固定状态顺序；允许跳转、回退及纠正。上一轮结束后须明确创建新一轮，不因换请求键绕过重复保护；若已有新一轮，不能把旧轮恢复为第二条未结束申请。删除历史后不承诺恢复其轮次或事件。

创建、状态、备注、下一步、实际投递时间调整按类别追加事件。同一次多字段保存可有多个类别事件，共用资源 revision/业务时间/系统时间；重放原请求不再次追加，没有字段变化也不伪造变化事件。事件接口只读，纠正当前信息追加新历史，不覆盖过去。用户可补录本次变化实际发生时间，系统记录时间只由服务器产生；时间线按 revision 记录顺序展示，较早发生的补录不会伪装成系统当时已有记录。

明确时刻必须带 UTC offset，规范化为 UTC，响应包含时区；界面按北京时间展示。行动日期是 Asia/Shanghai 的日期值，时间是另一个可空 `HH:mm`；仅日期不会补 00:00、09:00 或生成提醒。日期截止在北京时间当日结束后逾期，具体时间在该时刻之后逾期，完成/取消不逾期。服务器现代北京时间采用明确 UTC+08，避免依赖 Windows 可选 IANA 数据包；不承诺历史夏令时日历换算。

## 5. API 契约及安全

| 方法和路径 | 请求/响应 |
|---|---|
| `POST /api/v1/applications` | `{job_id,job_revision,idempotency_key,reapply:false,match_id:null}` → 准备投递记录；同键重试复用，换键重复明确 409 |
| `GET /api/v1/applications` | `q,status,job_id,sort,page,page_size` → items/total/page/page_size；排序 updated_desc/created_desc/submitted_desc/next_action_asc |
| `GET /api/v1/applications/{id}` | 当前记录、行动、真实逾期、最小匹配版本/失效原因 |
| `PUT /api/v1/applications/{id}` | `{revision,idempotency_key,status,submitted_at,notes,next_action,occurred_at?}` → 保存后的当前记录；完成/取消也走同一事务 |
| `GET /api/v1/applications/{id}/events` | page/page_size → 当前用户历史，分页有总数 |
| `DELETE /api/v1/applications/{id}` | `{revision,idempotency_key,confirmed:true}` → 204；删除内容保留摘要墓碑 |

请求使用 `extra=forbid`，禁止客户端 owner、事件、轮次和系统时间字段。服务端身份/CSRF/Origin 校验复用 S1，所有查询 owner 条件；未知/他人 UUID 返回相同安全 404，写入 CAS 不成功为 409。个人 API no-store，错误不含提交内容、SQL 参数或堆栈。JSON 由 React 文本呈现，不使用 HTML 注入。

写事务先锁 owner，再锁职位或申请；本地 SQLite 写锁和数据库唯一/FK 约束兜底。回执检查先于 revision，可从响应丢失恢复；同键不同操作/内容/资源为 409。复用旧请求只返回当前记录，不回滚之后的编辑。无自动请求重试；用户明确重试同内容保留原键。

有界限制：每用户最多 1000 条申请、每职位最多 100 轮、每条申请最多 5000 条事件、每用户 20000 个写回执；达到限制给明确错误，整笔操作回滚，不静默裁剪历史。删除仍允许执行，清 update 回执并可留最小删除墓碑。生产容量和长期墓碑整理策略尚未验收。

## 6. 中文界面与截图

“投递进度”是第五个导航入口；列表可搜索公司/岗位/城市，按状态筛选、排序、分页，分别提供首次空状态和筛选无结果。详情复用设计 Token、Radix 确认框和表单，宽屏信息/时间线分列，小屏单列。保存反馈明确，409 保留本地输入并提供只读最新版本，明确放弃后重载；危险操作需确认。没有假统计或复杂图表。

输入在当前组件内存，导航/退出/硬刷新有未保存提醒，不写 localStorage/sessionStorage。浏览器历史返回和关闭后的未保存草稿没有持久恢复承诺；服务器已保存内容刷新和重登录恢复。

截图由隔离真实 E2E 生成于 Git 忽略的 `frontend/artifacts/s4-1/`，可通过测试重生，均为合成资料：

- [桌面申请详情](../frontend/artifacts/s4-1/application-detail-1440.png)
- [手机申请详情](../frontend/artifacts/s4-1/application-detail-390.png)
- [桌面投递列表](../frontend/artifacts/s4-1/applications-list-1440-viewport.png)
- [手机投递列表](../frontend/artifacts/s4-1/applications-list-390-viewport.png)
- [409 保留输入](../frontend/artifacts/s4-1/application-conflict-1440.png)
- [手机逾期行动](../frontend/artifacts/s4-1/application-overdue-390.png)
- [关联历史匹配失效](../frontend/artifacts/s4-1/application-match-stale-1440.png)
- [真实 API 合成申请及事件 JSON](../frontend/artifacts/s4-1/synthetic-application.json)

## 7. 实测结果

| 验证 | 最终实测 |
|---|---|
| 完整后端 `unittest discover -s tests -v` | **531/531 通过**，208.913 秒，无失败/跳过；S3-2 的 474 + 新增 57 |
| 新申请领域与 0009 | **57/57 通过**并纳入上述全量：40 API/DB（含 4 配额、4 真实并发及 1 UTC 极端输入）、5 日期/时区、12 迁移；独立聚焦亦通过 |
| 独立匹配规则 | 原 **74/74** 保留并纳入全量，不重复累加 |
| 完整前端 Vitest | **99/99 通过**，10 个文件，12.44 秒；原 81 + 16 申请 UI + 2 API/代理 |
| 完整真实 Chromium | **29/29 通过**，1.9 分钟；原 25 + 新增 4，无自动失败重试 |
| OpenAPI / 生成客户端契约 | `api:check` 通过，后端实际 Schema、保存 JSON、生成 TypeScript 无漂移 |
| TypeScript / ESLint | `typecheck`、`lint` 均通过，无错误；构建完成后顺序检查 TypeScript，避免共用 `.next` 的竞争 |
| 生产构建 | `npm.cmd run build` 通过，包含 `/applications` 和 `/applications/[id]`；仅本机构建，没有部署 |
| 真实 HTTP 文件生命周期 | `tests/http_file_lifecycle_smoke.py` 通过，旧文件上传/下载/删除路径继续有效 |
| 差异、迁移、依赖 | `git diff --check` 通过；0001～0008 与两端依赖清单/锁文件无改动，无新增依赖 |

最终日志均位于忽略目录 `frontend/artifacts/s4-1/`：`backend-final.log`、`frontend-final.log`、`chromium-full.log`、`openapi-final.log`、`typescript-final.log`、`eslint-final.log`、`build-final.log`、`http-lifecycle-final.log`，每份保存实际 `exit_code=0`。截图 18 张，人工查看桌面详情、手机列表、平板完整详情及历史匹配失效，三种宽度自动验证页面无横向溢出。

真实 E2E 的四组场景：① 登录→打开职位→创建准备投递→已投递（时间仍未知）→明确补录时刻/备注/日期行动→时间线→刷新/重登→面试→笔试纠正→搜索/筛选；② 双页面并发 409 保留输入，并在服务端 commit 后中断响应，以原键重试只增加一条变化；③ 逾期定时行动完成/恢复/取消、XSS 文本、A/B/匿名/CSRF 和关联删除；④ 关联匹配→正式画像修改→历史失效而申请仍在→确认结束→确认创建第二轮→界面确认删除两条申请和职位。身份、申请和数据库均真实，未 Mock 申请接口。

首轮全量在测试仍补充时运行，为 **521/521**；最终冻结实现与测试后重新运行得到上述 **531/531**，不把早先数量冒充最终结果。新增 Chromium 初轮的一个失败是测试注册 B 后漏登录，按既有“注册不自动登录”规则修正夹具，再次全量通过，未放松权限断言。前端初轮的一项失败来自测试重复消费同一个 Response，修为每次独立响应后完整通过。

复查修复两项边界：只改备注时保留已有投递时刻的秒/毫秒，避免分钟输入造成隐性时间修改；ISO 年份边界经时区转换超出 UTC 可存范围时返回安全 422，保留原 revision/事件且不回显输入，能表示的极端日期仍可存取。另做 8 次创建申请与删除职位的真实并发探测，结果仅为创建成功/删除 409 或删除成功/创建 404，无丢失历史或孤立申请；有针对性的永久并发回归仍以上述四项测试为准。

可重复命令（PowerShell，仓库根；所有服务及迁移均使用临时隔离数据库/合成数据）：

```powershell
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///:memory:'
$env:OFFERPILOT_ENVIRONMENT = 'development'
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
& .\backend\.venv\Scripts\python.exe -B tests/http_file_lifecycle_smoke.py
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run test:e2e
npm.cmd --prefix frontend run api:check
npm.cmd --prefix frontend run lint
# E2E 与 build 顺序运行；typecheck 在 build 后执行。
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run typecheck
git diff --check
```

后端没有配置独立 formatter/linter/typechecker，不虚报已执行。DeepSeek 和招聘站真实请求为 0，没有读取或使用真实 Key、真实简历或实际申请数据；历史 Provider 验证沿用免费协议/故障桩，不代表真实模型质量。所有测试数据库/服务均由既有隔离夹具负责清理。

## 8. 要求覆盖映射

| 用户要求 | 可重复验证 |
|---|---|
| 1～5 从职位创建、收藏独立、防重复、投递时间、状态纠错 | `test_applications.py` 创建/收藏/重复/终态新轮/时刻/状态测试；`applications.spec.ts` 中文闭环和新轮 |
| 6～8 真实时间线、备注、行动创建完成取消 | API before/after、补录发生时间/记录时间、日期行动/清空/恢复；Chromium 完成取消与时间线 |
| 9 日期、时区与极端输入 | 5 项纯时间边界、API UTC 存储及输入校验、前端时间精度及日期测试 |
| 10～11 幂等与并发 409 | 4 种真实双线程/会话 DB 竞争、旧键重放；双页面编辑和已提交后 abort 的真实浏览器重试 |
| 12 重登恢复 | API 撤销会话后重登，Chromium 退出/重新登录/刷新 |
| 13～14 双用户和伪造 ID | 全部申请读写/事件/职位及匹配归属、CSRF/Origin、匿名、伪造 owner；Chromium A/B/匿名 |
| 15 职位关联删除 | API 终态也阻止删除、清申请后允许；数据库 RESTRICT；UI 确认删除 |
| 16 简历关联生命周期 | 实际合成 PDF 上传→候选确认→匹配→申请→删除原件，引用/引文清除、申请和事件保持 |
| 17 匹配失效 | 画像/JD 更改动态 stale、历史 ID/事件保持、硬冲突允许申请；真实网页失效提示 |
| 18 恶意备注/XSS | API 惰性 JSON、错误不回显；React 文本及 Chromium 不执行脚本、不访问外站 |
| 19 网络失败 | 同键请求回执；前端失败保留键；Chromium 服务端 commit 后响应 abort 再提交，只有一条变化 |
| 20 S1～S3 回归 | 全量后端、前端、Chromium、契约、lint、TypeScript、生产构建和 HTTP 文件生命周期 |

## 9. 未解决问题与 S4-2 前置条件

本机 SQLite/Chromium 验证不证明 PostgreSQL 锁/迁移、其他浏览器、真实移动设备、容量或公开发布已完成。没有连接实际开发/生产库。完整账户导出/注销、正式备份期限/删除重放、生产认证/文件解析隔离及既有供应商数据配置仍为上线前事项。

S4-2 需后续明确授权后再设计今日/逾期/近期行动聚合，复用申请的日期/时刻语义、pending 状态和 owner 查询，定义空状态和性能指标。先确认容量/保留、跨数据库并发及聚合筛选验收范围；不要将 Dashboard 或通知派发提前塞进本轮。

**本轮完成 S4-1 后立即停止。没有自动进入 S4-2、自动申请、Browser Agent、招聘抓取、真实模型请求、消息发送或公网部署。**
