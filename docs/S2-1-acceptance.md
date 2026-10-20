# S2-1 可信求职画像基础验收

**日期：** 2026-10-11（Asia/Shanghai）  
**范围：** 真实数据库的用户画像、候选审核、来源与版本、中文资料页。S2-2/S2-3 不执行。

## 1. 基线与本地检查点

先核查 PLAN/PRD/architecture/database/roadmap、S1-3 验收、README、当前模型/API/前端/抽取 Schema 与证据逻辑。S1 已验收但仍有有效未提交改动，与历史设计不完全相同；将所有有效源码和文档保留为本地提交 `571d95d89f651a0a54aa77c8c2eb079bc131c60f`。提交不含忽略的环境秘密、运行数据库、上传目录、依赖、构建或截图。未推送远程；S2-1 改动保留在工作区供审阅。

开发前原后端 125/125 通过。当前旧 `profiles` 按 resume_id 唯一、随简历级联删除，旧 `profile_facts` 没有确认语义；因此不把它们直接改成用户正式画像，也不将历史模型值静默提升为事实。

## 2. 文件与模块变化

| 文件/模块 | 变化 |
|---|---|
| backend/src/offerpilot_api/db/models.py；migrations/versions/0004_trusted_career_profile.py | 新画像、条目、候选三表与约束，0001～0003 保持原文件 |
| backend/src/offerpilot_api/profile/{schemas,services}.py | 严格值/批次/审核 Schema、领域授权、CAS、逐字段来源、审核和幂等候选入库 |
| backend/src/offerpilot_api/api/routes/profile.py；main.py | 三个公开端点、安全错误、已登录身份和 CSRF 接线 |
| resume_ingestion/lifecycle.py | 原删除意图事务接入候选/证据清理，保留 S1 物理删除与恢复边界 |
| frontend/src/app/profile；components/profile-{page,editor,candidates}.tsx | 中文分组编辑、完成提示、来源、审核与冲突反馈 |
| workspace-shell、dashboard、resume-detail | 增加资料入口、未保存提示，仅更新直接相关文案；未重做工作台 |
| frontend/src/lib/api/proxy/schema/profile-drafts；API Route Handler；openapi.json | 生成共享契约、PUT/审核白名单、内存草稿与会话清理 |
| tests/test_trusted_profile*.py；frontend profile.test.tsx/api/proxy tests；e2e/profile.spec.ts 与测试 launcher | 合成数据的领域/迁移/前端/真实 Chromium 验证 |
| PLAN/PRD/architecture/database/roadmap、抽取设计、README、frontend/DESIGN | 同步当前实现、生命周期语义及下一批前置条件 |

没有新增依赖、Redis、Celery、模型运行器或其他模块功能；锁文件不变。

## 3. 实际模型、API 与业务规则

- `career_profiles`：user_id 唯一，revision 非负，上限检查，版本与 UTC 更新时间。空 GET 返回 revision 0 和空数组，不制造经历；首次保存或内部入库才建立行。
- `career_profile_entries`：四类稳定 UUID 条目。教育 school/degree/major/start_date/end_date/end_date_kind；技能 name/level；项目 name/role/description/start_date/end_date/is_current；经历 organization/role/type/description/start_date/end_date/is_current。年月1900～2199、YYYY-MM，未知 null；不补日期、技能熟练度或年限。允许部分字段，拒绝空条目及矛盾时间。
- `profile_candidates`：独立 pending/confirmed/modified/rejected 审核池；记录简历、逐字段原文、Schema/抽取版本、批次摘要与审核时间。每批1～100，每用户总上限500；同简历批次键和序号唯一。相同键同内容复投无新行/无版本变化，不同内容409。既有旧表不自动迁移。

| 方法及端点 | 实际行为 |
|---|---|
| GET /api/v1/profile | 当前用户四组正式条目、独立候选及完成提示，no-store |
| PUT /api/v1/profile | revision 与四数组全量保存；新条目省略 id，缺席旧条目删除；部分字段可 null |
| POST /api/v1/profile/candidates/{id}/review | revision + confirm/modify/reject；只有 modify 携带同组 value，返回完整最新画像 |

所有写请求使用真实 Cookie 身份与 CSRF，拒绝客户端 user_id/来源元数据。entry/candidate ID 必须属于该用户，不存在与跨用户一致404。条件更新 revision 成功后才提交子表操作，过期409且回滚；审核与保存共用版本，已审候选重复审核409，后续从正式条目编辑。

来源区分用户填写、简历候选经用户确认、用户修订、来源简历已删除。未改字段保留来源；改字段转用户提供，旧引文 supports_current_value=false。不把模型置信度当正确率，新批次不接受 confidence。

supported 只表示有界字面证据，仍须主动审核；无依据/否定/歧义不能一键确认，允许用户明确修订或拒绝。服务端原文上下文检查防止只截取 `Java` 隐藏 `Not proficient in Java`；JavaScript、重复正反证据、全角/零宽及组合字符等反例有验证。每引文最多20处、每侧500字符；超命中或截断保守 needs_review。不能将这些词法护栏描述为完整语义理解。

删除简历先在 S1 意图事务清原文、历史派生、新候选和正式条目来源引文/引用，推进相关画像 revision。用户已确认/自行提供的当前值保留并明确来源已删；独立手工条目保留。实际文件清理失败503也不恢复证据，原 journal/lifecycle/重试规则不变；旧画像快照不能覆盖此版本。

## 4. 自动化与真实浏览器结果

| 验证 | 最终结果 |
|---|---|
| Python unittest discover（临时SQLite、真实身份） | **167/167 通过**，0失败、0跳过；60.863秒。原125 + 新42（34画像/API/领域 + 8迁移） |
| 既有真实 localhost HTTP lifecycle smoke | 通过，合成PDF、双账户、随机端口；进程/连接/临时目录清理完成 |
| npm run api:check | 后端 OpenAPI 与生成 TypeScript 一致 |
| npm run typecheck、lint | 通过 |
| npm run test（Vitest） | **30/30 通过**；原16 + 新14，8.11秒 |
| npm run build | Next生产构建成功，包含 /profile；无真实数据库读取 |
| npm run test:e2e（真实Chromium） | **12/12 通过**，54.1秒；原S1七场景 + 画像五场景 |
| git diff --check | 通过；旧迁移与前后端依赖锁文件无改动 |

真实 Chromium 使用新临时 SQLite 经 head 迁移、独立127.0.0.1 FastAPI/Next，主流程无 Mock API：

1. 空画像 → 只填学校并保存 → null 字段仍未知 → 四组继续补充 → 来源用户填写 → 刷新/退出重登录 → 再修改保存。
2. 测试内部入库三条合成候选，正式 skills 仍空；Python 用户确认，Java 缩短否定引文禁止原值确认、用户修订并核实，Go 无证据拒绝。审核状态和正式来源正确持久化。
3. 删除实际来源 PDF，候选/原文/引用消失、revision 增长，确认值及独立手工学校保留，页面显示来源已删除。
4. 匿名401、CSRF403、双用户空画像隔离、伪造 user_id 422、他人条目/候选与未知ID同404；写入不改变他人数据。
5. 同会话两个真实页面同版本编辑，第二次保存409、当地输入保留且服务器第一版本不被覆盖；导航与退出继续编辑保护有效。
6. 浏览器 Back 后回资料页恢复本标签页未保存草稿；远程新版出现时仍禁覆盖并显示最新值；明确放弃/退出清草稿，重登录只恢复数据库值。localStorage/sessionStorage 始终为空。

后端另覆盖双线程同revision只有一个200/另一个409，幂等入库、伪造来源、旧数据不确认、部分字段来源修订、实际目录阻塞删除503、日志/验证错误脱敏和数据库约束等。旧迁移测试只对齐两处 head 期待，及拒绝降级后先升回head再比当前metadata；原S1数据/权限断言保留。

实施中先复现再修复：模型序列化警告可能含候选值；超大revision绑定异常；短引文回避否定、Unicode词界与长/重复上下文问题。早期浏览器验收一处失败为来源文本断言不匹配，修正为实际可见来源并最终全套通过。合法JSON孤立surrogate另窄验安全422、无写入，不需改实现。

## 5. 实际页面与截图

均来自运行中的 Chromium 页面，仅含合成资料，无密码、邀请、Cookie、Key或真实简历。画像新增13张 PNG（11张整页 + 2张视口），与S1的24张截图同目录；每个主要画像状态测试1440/768/390无横向溢出，另截来源删除和409。截图被Git忽略，可运行全套E2E重生成。

| 状态 | 桌面1440 | 平板768 | 手机390 |
|---|---|---|---|
| 空画像 | [PNG](../frontend/artifacts/screenshots/profile-empty-1440.png) | [PNG](../frontend/artifacts/screenshots/profile-empty-768.png) | [PNG](../frontend/artifacts/screenshots/profile-empty-390.png) |
| 四组已保存 | [PNG](../frontend/artifacts/screenshots/profile-filled-1440.png) | [PNG](../frontend/artifacts/screenshots/profile-filled-768.png) | [PNG](../frontend/artifacts/screenshots/profile-filled-390.png) |
| 待审核候选 | [PNG](../frontend/artifacts/screenshots/profile-review-1440.png) | [PNG](../frontend/artifacts/screenshots/profile-review-768.png) | [PNG](../frontend/artifacts/screenshots/profile-review-390.png) |

另有 [桌面视口](../frontend/artifacts/screenshots/profile-filled-1440-viewport.png)、[手机视口](../frontend/artifacts/screenshots/profile-filled-390-viewport.png)、[409冲突](../frontend/artifacts/screenshots/profile-conflict-1440.png)、[来源已删除](../frontend/artifacts/screenshots/profile-source-deleted-1440.png)。实际查看桌面/手机保存状态、候选与冲突图，字体、间距、分组、来源和状态一致；截图前回滚动顶部避免整页图将fixed侧栏置于中部。

## 6. 迁移兼容与数据用途

0004 只新建三表，原0001～0003不变；新空库head和含owner/NULL-owner/简历原文/文件元信息/旧画像事实的0003快照均验证保持原值/关系，不认领、不自动确认。ORM metadata 与迁移head无漂移。新画像有唯一user、类别/状态/非负CHECK、FK；用户归属数据RESTRICT删除保护。0004无数据可安全降到0003，存在任何新画像/候选拒绝有损降级；offline降级拒绝。

新字段只为真实求职经历管理与后续匹配基础，不新增联系方式或模型Key收集。用户编辑删除条目，来源简历删除清候选/证据；本人确认值按用户资料保留，完整账户删除仍未实施。源引文只存必要片段，不进日志/追踪。DB硬删、unlink不等于SQLite空闲页/备份/磁盘安全擦除；正式保留期限、导出/注销与备份删除重放仍需上线前决定。

## 7. 剩余风险与S2-2前置条件

- 仅本机隔离 SQLite/Chromium 与指定宽度；未验证PostgreSQL并发/迁移、Firefox/WebKit、实体手机、完整无障碍、生产容量/HTTPS/灾备。S1的同步PDF解析、Windows ACL和恢复限制继续保留。
- 当前词法护栏会保守阻止部分真实语句确认，不能证明语义正确；日期/枚举/布尔等无可靠字面映射时需用户修订。后续需覆盖否定、归属、冲突、计划学习、日期归一化的版本化语义评测。
- 没有用户可触发的AI抽取；候选审核验收由隔离launcher固定合成夹具调用同一领域入口，夹具不属于正式app/OpenAPI或前端代理。现有四字段V1尚不能直接映射新结构。
- S2-2 必须先提供结构化抽取/转换和页段位置，供应商数据处理/最小发送预览及明确调用授权，会话BYOK保存/失效/清理，持久运行与调用账本、预算/有限重试/幂等/取消/删除迟到结果，再单独授权真实DeepSeek与质量验证。
- 账户导出/注销、保留/备份政策、解析隔离与上线安全仍是试点前条件。工具依赖原有peer/deprecation告警未在本批无关升级。

验收后本批Next/E2E Python均退出、临时E2E根目录清理；截图和本机构建留作复查。未修改实际开发/生产库，未调用付费模型、推送GitHub或公网部署。**S2-1完成后停止。**
