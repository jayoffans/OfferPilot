# S3-1 岗位导入与智能职位管理验收

开发与验证日期：2026-10-14；收口日期：2026-10-15（Asia/Shanghai）。范围：用户私有职位、JD 确定性结构化、人工条件审核、中文职位页面与持久管理。S3-2 匹配、S4 投递、Browser Agent 未开发。

## 1. 仓库、范围与交付判断

开始核实 `feature/resume-agent` 的 HEAD 为用户指定的 `de9040d`。工作区存在已验收的 S2 单样本报告、安全输入辅助及四份文档更新；检查源码语法/秘密形态、差异后将其保存为本地检查点 `de9f204`，没有覆盖或丢弃内容。S3-1 在 `codex/s3-1-job-management` 开发。开发前实际免费基线：后端324/324、前端49/49；历史Chromium18场景纳入本轮完整回归。

**交付判断：S3-1 达到本机开发验收标准。** 用户可以通过中文页面持久保存和管理岗位，将JD整理为可审核、可修订、有原文来源的条件。完整测试、契约、类型、lint、生产构建与差异检查通过；本轮只保留本地源码与检查点，未推送。实际结果如下，不以历史或局部成绩代替完整结果。

本轮不调用付费模型、不读取开发者Key、不访问招聘网站、不提交申请、不迁移实际开发/生产库、不推送GitHub或部署公网。保留S1/S2架构、画像/候选和已有依赖；S2首次正常合成样本的19/19、21/21仅为历史单样本结果，其余真实评测没有继续。

## 2. 实际文件与模型

| 范围 | 实际新增/修改文件（仓库相对路径） |
|---|---|
| 数据模型/接线 | 修改 `backend/src/offerpilot_api/db/models.py`、`main.py`；新增 `backend/migrations/versions/0007_private_jobs.py`、`api/routes/jobs.py` |
| 职位领域 | 新增 `backend/src/offerpilot_api/job_catalog/{__init__,schemas,services}.py` |
| JD规则接口 | 新增 `backend/src/offerpilot_api/job_intelligence/{__init__,parser}.py` |
| 中文页面 | 新增 `frontend/src/app/jobs/page.tsx`、`jobs/new/page.tsx`、`jobs/[id]/page.tsx`；新增 `components/{jobs-page,job-page,job-editor,jobs-ui}.tsx`，修改 `workspace-shell.tsx`、`dashboard.tsx`、`app/globals.css` |
| 契约/草稿 | 修改 `frontend/openapi.json`、`src/lib/{schema.d.ts,api.ts,proxy.ts}`；新增 `src/lib/job-drafts.ts`，类型从真实OpenAPI生成 |
| 后端验证 | 新增 `tests/test_job_catalog.py`、`test_job_parser.py`、`test_job_migrations.py`；修改 `test_{identity_migration,file_lifecycle_migration,trusted_profile_migration,extraction_migration,pdf_evaluation_pipeline}.py` 的当前head断言，保留原数据与安全断言 |
| 前端/浏览器验证 | 新增 `frontend/src/components/jobs.test.tsx`、`frontend/e2e/jobs.spec.ts`；修改 `src/lib/{api,proxy}.test.ts`、`scripts/e2e-backend.py` 的隔离迁移版本 |
| 文档 | 更新 README、PLAN、PRD、architecture、database、roadmap、frontend/DESIGN；新增本验收、`job-structure-design.md`、`S3-1-security-review.md` |

新增四表：`saved_jobs`（owner、基本字段、来源、收藏及版本），`job_jd_snapshots`（不可变原文/hash/版本），`job_requirements`（条件、分类、审核、证据坐标、来源及版本），`job_create_receipts`（用户隔离的创建幂等回执）。用途、必要性和保留/删除规则已同步 [数据库设计](database.md)。没有新增匹配、投递、向量库、模型密钥或个人事实字段。

## 3. API、结构化与审核流程

API沿用 `/api/v1`，全部使用显式Schema与既有Cookie身份。公开端点如下，职位及条件写入携带当前职位revision；创建携带幂等键，规则预览不产生持久数据但同样验证CSRF。

| 方法 | 路径及行为 |
|---|---|
| POST / GET | `/api/v1/jobs`：新增/本人分页列表 |
| POST | `/api/v1/jobs/parse-preview`：免费规则预览，全部pending |
| GET / PUT / DELETE | `/api/v1/jobs/{id}`：详情/完整编辑/删除 |
| PATCH | `/api/v1/jobs/{id}/favorite`：收藏/取消收藏 |
| GET / POST / PUT | `/api/v1/jobs/{id}/requirements`：读取/新增单条/完整保存 |
| PUT / DELETE | `/api/v1/jobs/{id}/requirements/{rid}`：单条修改/删除 |
| POST | `/api/v1/jobs/{id}/requirements/{rid}/review`：confirm/reopen |

列表支持名称/公司/城市搜索、工作类型、收藏、审核状态筛选和最近更新/添加/名称排序。空条件不计为已审核。写入均为原子事务；过期revision返回409且不覆盖。创建将完整请求摘要与岗位内容摘要分开：相同基本信息/JD复用岗位；同键异内容或冲突审核内容409，不静默丢审核。删除后最小回执阻止迟到创建请求复活职位。

`JDParser` Protocol + `jd-rules-1.0` 首版仅做有界词法整理，没有模型、工具执行或网络。14类条件覆盖学历、专业、届别、经验、地点、实习时长、技术、项目/实习经历、语言、职责、薪资、工作性质等；区分必须/优先/待核实。每条保留逐字引用、Unicode字符区间/行号、来源快照、Schema/解析/条目版本和审核时间。相关专业不展开范围，优先不升级，未知不补事实。复杂混合、否定和歧义保守待核实；超长句段不生成截断条件，原文完整保留并给出手工拆分提示。规则合成回归不代表通用JD理解质量。

实际中文流程：粘贴JD或手工录入 → 只填必要名称及已知基本信息 → 显式免费整理 → 对照原文逐条确认/修订/分类/删除/补充 → 保存未完成审核进度 → 列表与详情继续管理。中文页面修改条件会撤销原确认；API允许用户在同次修订请求中显式声明已审核。修改JD先保存新原文，再重新绑定并审核，旧条件全部pending/stale，旧快照仍可对照。没有全局自动确认或虚假匹配分数。

前端保存防重、身份切换取消旧请求、409保留输入及只读最新内容、离开/退出提醒；草稿仅当前标签页内存，按user/id隔离，退出清除。刷新/重登录读取持久数据。重复引文不猜来源；用户可填写字符起点，浏览器定位按Unicode码点转换。

## 4. 自动化、浏览器与安全实证

| 验证入口 | 最终实际结果 |
|---|---|
| 完整后端 unittest discover | **371/371通过**，214.089秒，0失败/跳过；历史324 + 本批47 |
| 完整前端 Vitest | **65/65通过**，11.70秒，0失败/跳过；历史49 + 本批16 |
| 完整真实 Chromium E2E | **22/22通过**，2.4分钟；历史18 + 本批4。最后JD审核按钮保护修正后受影响4场再次 **4/4通过**（24.9秒） |
| OpenAPI/TypeScript/ESLint | 通过；真实Schema及生成类型无漂移，类型/lint无错误或警告 |
| Next生产构建 | 2026-10-15收口重确认 **exit 0**；包含/jobs、/jobs/new、/jobs/[id]，只作本机构建验证 |
| 新旧迁移联合专项 | 40/40通过；新8项纳入全量后端 |
| 独立身份/API安全抽查 | 130个断言通过，不算130个独立用例 |
| 既有真实localhost HTTP文件生命周期 | 通过，原S1/S2联调保持 |
| git diff --check / 历史迁移与锁文件 | 通过；0001～0006及两端锁文件无改动，无新增依赖 |

本机测试日志位于忽略的 `frontend/artifacts/s3-1/`：`backend-final-verified.log`、`frontend-final-pass.log`、`chromium-final-verified.log`、`jobs-e2e-final-pass.log`、`http-smoke-final.log`、`job-migrations-final.log`、`independent-security-probe.json`、`build-closure-2026-10-15.log`及对应exit_code记录，完整命令沿用 README。Python未配置独立formatter/linter/typechecker，不虚报检查；现有Starlette/httpx弃用与NO_COLOR提示未在本批无关升级。

新增真实Chromium四场使用实际FastAPI/Next/新临时SQLite经0007迁移与合成JD，职位API没有Mock：

1. 登录 → 添加 → 粘贴JD → 填基本信息 → 规则结果pending、Java优先/Python必须/歧义待核实 → 确认与修订/补充 → 部分保存 → 返回列表搜索 → 重新打开/刷新 → 退出重登录 → 编辑基本信息和已保存条件 → 收藏/取消收藏 → 删除。逐条重算引文，正式画像仍空。
2. 同会话两页面同revision编辑，第一次保存、第二次409；冲突输入保留，数据库第一份不变。
3. 无CSRF拒绝、危险链接422、创建重放同ID、JD脚本不执行/不请求外部域名、来源链接noopener/noreferrer；匿名401、A/B与未知资源同404，其他用户不能收藏/删除。
4. 修改JD推进revision和jd_version，原确认pending/stale；无JD手工职位可持久保存。

后端/API专项补充单条要求增改删/确认重开、条件分类与来源修订、伪造owner/他人条件ID、证据错误原子回滚、旧来源确认409、重复引文定位、Unicode、真实双线程CAS、同键/不同键并发创建、同JD去重、删除级联/墓碑重放、安全错误不回显JD及长句/上限反例。独立证据与迁移审查详见 [安全报告](S3-1-security-review.md)。

实施中实际发现并修复：规则编号处理误删届别数字、标题上下文与否定/疑问保守分类、长段Schema错误路径、SQL NULL导致证据CHECK漏校验、空条件误入已审核筛选、URL编码/IP变体、重复引文和Unicode位置、账户切换迟到响应。早期浏览器失败为旧launcher迁移断言、字段定位及SPA导航尚未完成就刷新；纠正后复验。完整后端首轮唯一失败为旧PDF免费评测测试硬编码0006，改为读取当前迁移head，原脱敏、候选及证据断言保留，没有执行真实评测。补充JD修改后先保存再确认的保护时，一次漏接按钮禁用条件被扩展的单元场景发现；修正后65/65与职位4/4复验通过。

## 5. 中文页面与响应式截图

真实Chromium生成24张职位PNG，仅合成岗位/账户，不包含密码、邀请、Cookie、Key或真实简历；截图被Git忽略，可运行E2E重生。1440、768、390宽度自动检查无横向溢出；已人工查看桌面审核/详情、平板列表、手机审核/详情与冲突截图，沿用既有字体、Token、侧栏/移动导航、表单和反馈样式。截图前释放输入焦点并回顶部。

| 页面 | 1440 | 768 | 390 |
|---|---|---|---|
| 空职位列表 | [PNG](../frontend/artifacts/screenshots/jobs-empty-1440.png) | [PNG](../frontend/artifacts/screenshots/jobs-empty-768.png) | [PNG](../frontend/artifacts/screenshots/jobs-empty-390.png) |
| 导入/条件审核 | [PNG](../frontend/artifacts/screenshots/jobs-review-1440.png) | [PNG](../frontend/artifacts/screenshots/jobs-review-768.png) | [PNG](../frontend/artifacts/screenshots/jobs-review-390.png) |
| 已保存详情/证据 | [PNG](../frontend/artifacts/screenshots/jobs-detail-1440.png) | [PNG](../frontend/artifacts/screenshots/jobs-detail-768.png) | [PNG](../frontend/artifacts/screenshots/jobs-detail-390.png) |
| 搜索后的列表 | [PNG](../frontend/artifacts/screenshots/jobs-list-1440.png) | [PNG](../frontend/artifacts/screenshots/jobs-list-768.png) | [PNG](../frontend/artifacts/screenshots/jobs-list-390.png) |

另有 [409冲突](../frontend/artifacts/screenshots/jobs-conflict-1440.png)、[JD更新失效](../frontend/artifacts/screenshots/jobs-jd-stale-390.png) 及1440/390视口图；不是完整无障碍认证或实体设备用户研究。

## 6. 迁移兼容、未解决事项与S3-2条件

0007只新建四表，不改历史0001～0006；空库和旧0006全表逐行快照向前验证保持用户、原文件元信息/正文、正式画像revision、pending候选/原文、任务和合成授权计量关联。没有自动认领NULL-owner、重新解析或确认旧数据。ORM与迁移的CHECK/FK/索引/唯一约束一致，实际外键完整性通过。空新表可降到0006并重升；有职位或仅有删除回执时拒绝有损降级，offline降级不可绕过在线检查。

未解决的实际边界：规则不能理解所有复合句，用户仍需人工拆分/补充；每人1000职位、每JD30,000字符、每岗位100条件/100快照；只实测SQLite与本机Chromium，PostgreSQL、生产容量和其他浏览器未验收。账户级导出/注销、回执保留期限、备份删除重放沿用后续上线条件；逻辑硬删不代表磁盘安全擦除。S2总体模型质量与供应商生产条件没有因本轮解除。

S3-2须单独授权，以S2正式画像和当前JD已审核、语义适用的条件为输入；保留手工来源、未知/待核实与缺证据，不把资料没写视为能力不满足。需要确定性四态规则及独立标注反例，匹配快照绑定画像revision、JD/条件/Schema/解析/规则版本，依据变化后失效；解释引用双方证据。不得将单一分数替代资格、技能经历和未知项。本轮不创建匹配或投递数据结构。

本批Chromium服务正常退出并清理其新建临时数据库/对象目录；收口核对3001/8001无监听。截图与本机构建保留供复核，全部为忽略文件。此前S2历史临时目录的限制属于原报告记录，本轮没有继续处理或将其宣称已清理。

**停止点：完成S3-1后停止，不自动进入S3-2。**
