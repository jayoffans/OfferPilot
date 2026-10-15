# S3-2 可信岗位匹配引擎验收

日期：2026-10-15（Asia/Shanghai）。开发起点：`codex/s3-1-job-management` / `2f44f2f`，起始工作区干净。保留该分支开发，未推送。测试结果以末尾实测表为准。

**交付判断：S3-2 达到本机功能验收标准。** 中文网页已完成正式画像 → 已审核 JD → 确定性匹配 → 双方证据 → 依据变化 → 重新计算的真实闭环。最终后端474/474、前端81/81、Chromium25/25通过；本轮仅迁移隔离测试库，不涉及实际开发/生产库或公开发布。

## 1. 产品范围与判断原则

用户在“我的资料”保存正式画像，逐条审核已保存岗位的 JD 条件后，在职位详情计算匹配。正式画像来自用户手工保存或候选经确认/修订后的正式条目；pending、rejected 等候选及历史按简历派生画像均不参与。系统不访问模型、招聘链接或其他用户资料。

**匹配结果是辅助决策，不是录用概率。** 首版不生成分数、不按技能数量排序，不隐藏、删除或拒绝岗位，不修改用户事实。所有已审核且属于当前 JD 的条件均产生四态结果；未审核和旧 JD 条件在结果中单独列出排除原因。分类仍为待核实的条件只提示核实，不变成硬性资格。

三种结论由逐项结果确定：必须资格出现已确认事实与明确要求的冲突时提示“存在明确硬性条件冲突”；必须资格仍有未知或部分满足时提示“建议先核实关键条件”；其余提示“可以进一步考虑”。优先条件与个人偏好分别解释，不能单独导致硬性资格冲突。

## 2. 规则、证据与局限

规则版本固定为 `match-rules-1.0`，输出 Schema `1.0`。纯函数与 HTTP、数据库分离；不引入依赖、向量数据库或 Agent 框架。规则修订须升级版本并运行反例回归。

| 类型 | 首版规则与保守边界 |
|---|---|
| 学历 | 控制层级表与完整学历表述比较；实际毕业、预计毕业和在读分别判断。本科在读不能证明已取得本科；硕士在读也不自动符合“本科在读”。普通较低学历条目不代表最高学历，缺少更高学历证据时不得直接拒绝。“研究生”不擅自等同硕士，肄业/结业等不能证明已取得学历 |
| 毕业届别 | 明确的毕业年月及实际/预计语义、明确年份或年份范围；“应届”定义不明时需要招聘方确认，不依赖当前年份猜测 |
| 专业 | 精确专业或画像中明确声明的类别；“相关专业”不擅自扩展，没有审核过的专业分类树 |
| 技术 | 小规模受控别名与有方向的版本关系；Java/JavaScript、React/React Native 分开；否定、计划学习、缺少技能证据不作为已掌握。非空程度采用受控完整表述，未知程度不能当肯定证据；矛盾条目需要核实，混合且/或逻辑不猜作用范围 |
| 项目与经历 | 每条证据绑定正式条目 ID；同名项目不合并，不把项目当工作或实习当全职；普通“工作”条目也不自动证明全职。技术需直接使用动作和本人归属，旁观/他人使用不符合。起止仅到月份时使用 `max(月份差−1,0)` 的保守完整月下界，不推算未知/进行中结束时间，不累计跨条目 |
| 工作条件 | 明确条件与用户确认偏好比较；缺少字段、地点范围、复杂时间要求均保留未知；偏好冲突不升级为资格冲突 |
| 薪资 | 只比较明确币种、计薪周期及税前/税后相同的范围；日/月/年之间不换算，税制不明不作可靠匹配 |
| 复合要求 | 只在所有已支持的语义有证据时满足；超出语法覆盖的附加资格、复杂替代或模糊表达提示拆分或核实，不能凭命中一个关键词给确定结论 |

每项保留要求 ID/原文、必须/优先/待核实、四态、理由、具体画像条目及字段值、JD 引文和 Unicode 字符位置/行号、规则版本、待核实问题。用户手工补充的 JD 条件显示用户提供、原文定位未知，不伪造引用。画像原文只保留必要字段的支持片段，不保存完整简历；删除来源简历时同步清除历史匹配里的来源引用和原文，已确认事实值按现有产品规则保留。

规则是可审计的有限语法，不是通用自然语言理解。保守的未知结果是当前规则边界，用户应拆分复杂条件、补充真实画像事实或向招聘方核实。原文引用说明来源，不证明招聘信息或用户事实经第三方核验。重新计算使用当前正式依据；历史保留结论和相关最小字段快照，不是完整旧画像备份，不能恢复已删除的来源原文。跨规则版本需要相应旧版代码才能回放旧算法。

## 3. 数据与 API

实际文件与职责（相对仓库路径）：

| 文件 | 修改理由 |
|---|---|
| 新增 `backend/src/offerpilot_api/job_intelligence/matching.py`、`match_schemas.py` | 纯规则、受控别名、四态、综合建议和双方证据契约 |
| 新增 `job_intelligence/match_services.py`、`match_api_schemas.py`、`api/routes/job_matches.py` | 领域事务、owner、双版本围栏、幂等、历史/失效、HTTP 契约；路由不承载匹配规则 |
| 修改 `backend/src/offerpilot_api/{db/models.py,main.py}` | 新实体及路由、安全错误装配 |
| 新增 `backend/migrations/versions/0008_trusted_job_matches.py` | 向前增加偏好列、匹配及回执，保护有数据的降级 |
| 修改 `profile/{schemas.py,services.py}`、`api/routes/profile.py` | 最小偏好独立保存及既有删除事务里的历史证据清理 |
| 新增 `frontend/src/components/job-match.tsx`、`profile-preferences.tsx`；修改 `job-page.tsx`、`profile-page.tsx`、`app/globals.css` | 复用现有设计系统接入中文匹配/偏好、异步状态、证据展开与响应式 |
| 修改 `frontend/src/lib/{api.ts,proxy.ts}`、`frontend/openapi.json`、`src/lib/schema.d.ts` | 严格代理白名单与真实 OpenAPI 生成客户端类型，不复制手写契约 |
| 新增 `tests/test_matching_rules.py`、`test_job_matches.py`、`test_job_match_migration.py` | 独立规则反例、真实 API/DB、安全并发及旧库向前保持测试 |
| 修改 `tests/test_job_migrations.py`、`test_trusted_profile_migration.py`、`frontend/scripts/e2e-backend.py` | 适配新 head/历史降级断言，在相应旧版本仍验证旧结构，再恢复 head 供当前 ORM 读取；未修改旧迁移或放松原业务断言 |
| 新增 `frontend/e2e/matching.spec.ts`、`job-match.test.tsx`、`profile-preferences.test.tsx`；修改 `profile.test.tsx`、`jobs.test.tsx`、`api.test.ts`、`proxy.test.ts` | 真实浏览器闭环、界面错误/版本/证据与输入边界回归；旧职位组件测试显式隔离新面板，真实接线由 E2E 验证 |
| README、PLAN、PRD、architecture、database、roadmap、frontend/DESIGN、本文件 | 同步当前能力、规则边界、个人数据用途/生命周期、运行命令与实测记录；保留历史报告 |

向前迁移 `0008_trusted_job_matches`，不改 0001～0007。正式画像增加最小 `preferences` JSON：城市、工作类型、方向、薪资结构、每周天数、实习月数；默认空，独立保存，不要求重新填写履历。用途仅为本人岗位决策，用户可清空，字段值不进入日志。

`job_matches` 保存 owner、岗位、画像 revision、岗位 revision、JD 版本、规则/Schema 版本、逐项结果与综合建议、创建时间。依据组合唯一，重复计算复用同一结果。`job_match_receipts` 保存 owner 隔离的请求键和摘要，避免未知网络结果导致重复记录；同键不同请求返回冲突。完整历史不覆盖，最新读取与当前画像、岗位、规则版本比较并返回失效原因。收藏和候选审核/入库也推进既有资源 revision，因此可能保守提示重新计算；不另增有漂移风险的第二套正式版本。

| 方法与路径 | 作用 |
|---|---|
| `PUT /api/v1/profile/preferences` | revision + preferences 独立保存，复用正式画像 revision |
| `POST /api/v1/jobs/{id}/matches` | 只接受画像/岗位 revision、幂等键；服务端读取可信数据计算 |
| `GET /api/v1/jobs/{id}/matches/latest` | 本人最新结果；无历史时 null；动态返回 stale 和依据变化原因 |
| `GET /api/v1/jobs/{id}/matches/{match_id}` | 本人指定结果和逐项证据，旧结果仍显示失效状态 |
| `GET /api/v1/jobs/{id}/matches` | 有界历史，便于核对版本变化 |

所有路径复用服务端会话、对象级 owner、显式 Schema、安全错误和禁止缓存；写请求有 CSRF/Origin 检查。客户端不能传入画像事实、用户归属或来源。计算事务对正式画像与岗位做版本围栏，唯一约束处理重复并发。删除岗位级联删除匹配与回执；完整账户导出/注销尚未交付，未来必须纳入新表、偏好、备份与删除重放。没有新增自动保留期限；不能把 SQLite 逻辑删除称为磁盘安全擦除。

## 4. 中文界面与真实闭环

现有职位详情增加“岗位匹配”区，包含综合结论、主要原因、计算时间、画像/JD/规则版本、逐类可展开证据和可操作的核实建议。状态均有文字；无正式画像、无当前已审核条件、首次使用、请求中、失败及版本变化有明确提示。提供修改画像、审核 JD、重新计算及既有收藏操作，不增加投递假按钮。

真实 Chromium 使用合成用户、合成正式画像、合成 JD 与临时 SQLite，经 Alembic head 向前迁移；匹配和画像请求没有 Mock。核心场景：登录 → 填写并保存本科在读/2027 预计毕业/Python 3 → 保存待审核岗位 → 页面逐条审核 → 匹配并展开证据 → 页面修改预计毕业为 2028 → 旧结果提示依据变化 → 重算出现届别硬性冲突。另验证未确认 AI 候选不参与、JD 更新失效、无 CSRF、伪造画像、A/B 隔离及幂等重算。

合成结果和截图由测试生成于 `frontend/artifacts/s3-2/`（Git 忽略，可重生）。本轮生成 24 张匹配 PNG，人工查看了桌面双方证据、手机证据、平板偏好及失效/冲突状态，自动检查三种宽度无横向溢出。

- [桌面综合建议及双方证据](../frontend/artifacts/s3-2/matching-evidence-1440-viewport.png)
- [手机证据展开](../frontend/artifacts/s3-2/matching-evidence-390-viewport.png)
- [依据变化](../frontend/artifacts/s3-2/matching-stale-1440-viewport.png)
- [重新计算的硬性冲突](../frontend/artifacts/s3-2/matching-hard-conflict-1440-viewport.png)
- [偏好与资格分开](../frontend/artifacts/s3-2/matching-preferences-768.png)
- [真实 API 返回的合成结果 JSON](../frontend/artifacts/s3-2/synthetic-match-example.json)

以下来自上述真实 API/数据库结果，不是 Mock 匹配。画像 v1：本科、预计 2027-06 毕业、Python 3/熟悉；岗位 revision 2 / JD v1，三项已确认、Java 优先一项 pending。

| 要求 | 画像 v1 结果 | 改预计毕业为 2028-06 后的 v2 |
|---|---|---|
| 本科在读 | 明确满足；在读身份，不称已取得本科 | 明确满足 |
| 2027届毕业生 | 明确满足；2027 在明确范围内 | 明确不满足；已确认的预计届别与必须条件冲突 |
| 必须熟悉 Python | 明确满足；正式 Python 3 与熟悉程度，由受控有向版本关系支持 | 明确满足 |
| 熟悉 Java 优先（未审核） | 列于未纳入条件，不参与推导 | 同前 |
| 综合建议 | 可以进一步考虑 | 存在明确硬性条件冲突 |

原结果在画像保存后读为 `stale=true / profile_changed`；重算得到不同结果 ID，旧结果保留。相同 v2 依据换请求键重算仍返回同一 ID。新结果不会替用户删除、隐藏、拒绝或投递这个岗位。

追踪例：届别项保留正式教育条目 UUID、`end_date=2027-06` / `end_date_kind=expected`（用户填写），对应 JD 引文 `2027届毕业生`、JD v1、第2行、Unicode 字符 `[5,13)`，规则 `match-rules-1.0`。修改后新快照保存 `2028-06`，旧快照仍保留 `2027-06` 及其 revision。候选审核来源另经真实后端测试验证：来源是用户确认的简历字段、字段引文、简历 ID；修订字段归用户填写。删除来源同时清当前/历史引文和简历 ID，保留已确认字段值及归属。

## 5. 实测结果

| 验证 | 最终实际结果 |
|---|---|
| 固定检查点基线 | `2f44f2f` 临时独立快照，后端 **371/371**，155.324秒；原前端 **65/65**，11.69秒；原22个浏览器场景纳入本轮全量 |
| 完整后端 `unittest discover -s tests -v` | **474/474通过**，176.025秒，无失败/跳过；原371 + 新74规则 + 新21 API/DB + 新8迁移 |
| 独立规则 | **74/74通过**，纳入全量；额外顺序/分类等组合以subTest验证，不虚增独立测试数 |
| 匹配 API/DB | **21/21通过**，含最后来源修订归属闭环，纳入全量；无Mock身份或Mock数据库 |
| 新旧迁移联合 | **48/48通过**，旧40与新8；新8纳入全量，不重复累加 |
| 完整前端 Vitest | **81/81通过**，9个测试文件，13.17秒；原65+新增16，最终规则升级幂等意图和中文金额依据显示亦覆盖 |
| 完整真实 Chromium | **25/25通过**，1.7分钟；原22+新增3。最后截图定位修正后受影响3场再次 **3/3通过**，18.2秒 |
| OpenAPI生成/契约 | `api:check` 通过，实际后端 Schema 与 `openapi.json`、生成 `schema.d.ts` 无漂移 |
| TypeScript / ESLint | `typecheck`、`lint` 通过，均无错误；后端未配置独立formatter/linter/typechecker，不虚报执行 |
| Production Build | `npm.cmd run build` 通过，包含原有页面和职位详情；构建后顺序执行 `typecheck` 通过，仅本机构建，不部署 |
| 真实 HTTP 文件生命周期 | `tests/http_file_lifecycle_smoke.py` 通过，覆盖既有真实localhost服务/文件路径 |
| 历史迁移/依赖/差异 | 0001～0007、两端锁文件与依赖清单保持；`git diff --check` 通过，无新增依赖 |

日志位于忽略的 `frontend/artifacts/s3-2/`：`backend-final-verified.log`、`frontend-final-verified.log`、`chromium-final-verified.log`、`chromium-screenshot-verified.log`、`production-build-final-verified.log`、`typescript-final-verified.log`、`eslint-final.log`、`openapi-final.log`、`http-lifecycle.log` 及对应exit_code记录。固定基线另存 `frontend/artifacts/s3-2-baseline-isolated.log`。源码以本地工作区交付，没有推送或部署。

可重复命令（PowerShell，仓库根目录；所有验证自行创建临时SQLite/合成文件）：

```powershell
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///:memory:'
$env:OFFERPILOT_ENVIRONMENT = 'development'
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
& .\backend\.venv\Scripts\python.exe -B tests/http_file_lifecycle_smoke.py
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run test:e2e
npm.cmd --prefix frontend run api:check
npm.cmd --prefix frontend run lint
# 与E2E/next dev顺序执行：它们共用.next生成目录。
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run typecheck
git diff --check
```

本轮 DeepSeek/付费模型请求0次，没有读取或使用开发者Key；匹配模块无Provider或网络调用。历史模型测试继续使用明确的免费协议/故障桩，不代表供应商真实质量。

覆盖用户要求的测试矩阵：

| 要求 | 可重复验证位置 |
|---|---|
| 学历满足/不足、在读/取得、届别范围、未知和相关专业 | `test_matching_rules.py` 的学历/届别/专业组；含肄业、泛研究生、高层次在读不等于本科在读、最高学历自身冲突反例 |
| Java/JavaScript、React/RN、Python版本、别名、否定/计划、缺证据 | 同文件技能组；含混合且/或、未知程度、同技能正负/未知条目顺序及 must/preferred 组合，错误拒绝与错误满足均验证 |
| 同名项目、多经历归属、实习/工作/全职、精确时长边界 | 同文件经历组；含旁观他人技术、只有名称、课程项目、跨条目拼接、月份恰好等于阈值的反例 |
| 城市、类型、方向、薪资和可用时间；偏好/优先不拒绝 | 同文件偏好/综合组；薪资双税基/多周期/额外奖金、不同单位不换算；前端偏好真实 E2E |
| 复合条件和未知字段，所有当前已审核条件产生一项结果 | 同文件 Schema/完备性/重放组；未知内容不得只凭关键词满足 |
| 画像/JD/规则变更失效，历史保留和旧重放 | `test_job_matches.py`、真实 `matching.spec.ts`；规则升级客户端请求键另有组件测试 |
| 重复及真实并发请求 | API 双线程同键/不同键，结果一条；画像编辑串行后旧结果失效，受影响接口回归 |
| A/B、匿名、CSRF、非法 ID、伪造画像和 owner | API 所有匹配路由与偏好路径，真实浏览器同源代理/API；跨用户和未知资源均安全拒绝 |
| 未确认 AI 候选、来源删除与用户修订归属 | 正式候选入库后技能仍空；真实上传→确认→修订→匹配→删除 API 及历史证据清理；浏览器 pending-only 场景 |
| 原功能回归与迁移 | 全后端、全前端、全 Chromium；空库及有旧0007合成数据逐表快照、外键、唯一、约束及安全降级 |

初轮问题均保留失败记录后修正：基线运行期间并行开发引入了新迁移，最初“基线”不再是固定源码；改为 `git archive 2f44f2f` 的临时独立快照，实际 371/371 通过。首轮新浏览器测试对非法 UUID 预期后端 422，但同源代理按白名单先返回 404，按实际安全边界修正断言（后端 422 仍有独立测试）。一次 TypeScript 与 Next 构建并行时生成类型短暂被重建，改为构建后顺序执行类型检查；未改源代码绕过检查。规则审查发现的误判按反例修复后全量回归，没有降低原有功能断言。

未执行/局限：PostgreSQL迁移和行锁、非Chromium浏览器、真实手机/平板、生产容量与公开部署均未验证。偏好未保存草稿只在当前组件内存，常规导航/刷新/退出有提醒，浏览器原生后退不增加额外恢复机制。有限规则会产生待核实项，无法证明真实招聘资格或录用结果；薪资及经历边界详见第2节。每职位最多200份结果、1000幂等回执，达到上限给出明确冲突提示；没有自动清理或账户级导出/注销。原有Starlette/httpx弃用、NO_COLOR等运行提示未作无关升级。

## 6. S4 前置条件与停止点

S4 需另行授权，先确定申请状态/事件/时间线 Schema、本人对象授权、幂等/并发、UTC 存储与上海时区展示及岗位删除后的申请快照政策。收藏不等于投递，匹配结论不触发提交或自动状态变更。完整 MVP 对外发布仍需账户导出/注销、隐私与保留/备份策略、生产数据库/容量及既有模型数据配置等独立验收。

本轮止于 S3-2，不开发 S4、Browser Agent、招聘抓取或自动投递；不进行真实 DeepSeek 调用、连接生产库、推送或公网部署。
