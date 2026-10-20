# S2-2A 可信 AI 简历抽取流水线验收

**日期：** 2026-10-12（Asia/Shanghai）

**范围：** 合成 PDF、Mock Provider、严格 Schema V2、服务端证据定位、持久任务与 S2-1 候选审核。真实 DeepSeek、付费调用、Key/BYOK、S3、Browser Agent 与部署不在本批范围。

**验收结论：** 达到本机开发验收标准。合成中文 PDF 的完整流水线和用户确认已由真实 API/数据库/Chromium 验证；确认前正式画像为空。后端 222/222、前端 39/39、完整 Chromium 16/16 通过。Mock 成绩不代表真实 AI 质量。

## 1. 执行前审查与检查点

在 `feature/resume-agent` 审查 PRD、architecture、database、roadmap、PLAN、S2-1 验收、上传与删除生命周期、DeepSeek Adapter、四字段 V1、正式画像/候选、0001～0004、前端和自动测试。后端原基线 167/167 通过（60.354 秒），前端原 Vitest 30/30 通过（19.75 秒）。

S2-1 的 40 个有效源码、测试与文档文件保存为本地提交 `d91582400caecae599574bb8ed1f8f8af438016d`。未重置或覆盖用户改动，未推送。忽略的环境秘密、实际数据库、上传原件、依赖、构建与截图未提交。本批变更保留在该检查点后的工作区。

真实基线存在三个接线缺口：上传只存合并文本、不保留页段；内存 V1 不能表达结构化教育/经历；S2-1 内部候选入库自行提交事务，不能和持久运行的成功状态原子提交。本批分别扩展解析布局、V2 与事务归属，继续使用唯一的 `career_profiles/career_profile_entries` 和既有用户审核接口。

### 修改文件及模块

以下路径均相对仓库根目录；完整变更保留工作区供审查。

| 文件范围 | 实际变化 |
|---|---|
| backend/src/offerpilot_api/resume_ingestion/{extraction_schema,evidence,extractor_v2,synthetic_fixtures}.py；prompts/resume_extract_v2.txt | V2、固定合成输出、证据定位/风险/映射；旧 extractor.py/V1/Prompt 保留 |
| backend/src/offerpilot_api/agent_runtime/{extraction_runs,extraction_schemas,process_extractions}.py；providers/mock.py | 持久状态机、预算/领取/提交、HTTP 契约、有限 CLI、无网络 Mock；DeepSeek Adapter 保留 |
| backend/src/offerpilot_api/db/models.py；migrations/versions/0005_resume_extraction_runs.py | 页段、运行及追溯结构/约束；旧 0001～0004 保留 |
| backend/src/offerpilot_api/{api/routes/extraction.py,main.py,core/config.py,services/resume.py,resume_ingestion/lifecycle.py} | 六 API、执行器开关、解析布局、删除围栏与安全错误 |
| backend/src/offerpilot_api/profile/{schemas,services}.py | 外层事务入库、严格映射/运行归属、来源版本重验及永久 Mock 来源标记；复用原审核 |
| backend/evaluations/{evaluate_resume_extraction_v2,resume_extraction_v2_gold}.py；s2-2a-synthetic-report.json | 独立 gold、可重复免费评测与实际错误报告 |
| tests/test_{resume_extraction_v2,extraction_runs,extraction_migration}.py | 新 29 核心/候选、20 工作流、6 迁移验证 |
| tests/test_{identity_migration,file_lifecycle_migration,trusted_profile_migration}.py | head 期望及新 inline FK 的 PRAGMA 比对；降级后恢复 head 才使用最新 ORM，原旧数据断言保留 |
| frontend/src/components/{resume-extraction,candidate-extraction-details}.tsx；{resume-detail,profile-candidates,profile-editor}.tsx；src/app/resumes/[id]/page.tsx；src/app/globals.css | 最小测试面板、V2 原文位置/风险、来源标签和限定样式 |
| frontend/{openapi.json,src/lib/schema.d.ts,src/lib/api.ts,src/lib/proxy.ts} | 真实生成契约与明确方法/路径白名单 |
| frontend/{src/components/resume-extraction.test.tsx,src/components/profile.test.tsx,src/lib/api.test.ts,src/lib/proxy.test.ts,e2e/extraction.spec.ts,e2e/workspace.spec.ts,e2e/global-setup.ts,scripts/e2e-backend.py} | 前端边界、真实四场新 E2E、仅隔离 launcher 的故障控制与合成 PDF |
| README.md；docs/{PLAN,PRD,architecture,database,roadmap,resume-extraction-design}.md；本验收；frontend/DESIGN.md | 当前能力、执行/评测、数据用途、结果、限制与 S2-2B 前置条件同步 |

前后端依赖/锁文件、旧 DeepSeek Provider、0001～0004、无关业务模块保持原文件；没有新 Agent 框架或队列。

## 2. Schema V2 与候选映射

V1 与 DeepSeek Adapter 保留，V2 单独版本化。V2 顶层包含 education、skills、projects、experiences；所有键必须出现，缺失事实为 null、集合为 []，拒绝额外字段及类型强制转换。叶子保留原值、逐字 source_text、自报 confidence 和 affirmed/negated/planned/uncertain；confidence 只用于追溯模型报告，不控制确认，也不解释为事实正确概率。

| V2 内容 | S2-1 正式字段映射与边界 |
|---|---|
| 学校、学历、专业、就读起止、毕业月份及状态 | school/degree/major/start_date；仅明确毕业月份映射 end_date，明确预计/实际映射 end_date_kind；就读结束不推断毕业 |
| 技能名称、水平、否定/计划/不确定断言 | name/level；版本化有限别名归一化，Java 与 JavaScript 分开；风险阻止原值确认 |
| 项目名称、角色、日期、进行中、技术栈、真实工作项 | name/role/start_date/end_date/is_current；description 使用有界连续原文，技术栈及工作项原字段路径保留 |
| 公司、岗位、类型、日期、职责与可证实成果 | organization/role/experience_type/日期/current；职责与成果以原文映射 description，保留各叶子归属 |

中文及 `YYYY-M`、斜线、点分隔年月经服务端有界归一化；只有年份不会补月份，未知结束时间不会变“至今”。字段/日期冲突、无法识别的状态或不可靠归属保守要求核实。映射候选仍为 pending，不修改正式条目；confirm/modify/reject 使用 S2-1 revision、对象授权和审核服务，只有用户明确动作才产生正式值。

## 3. 证据验证策略及局限

PDF 页文本按既有最终 raw_text 字符串保存服务端页范围和段范围；偏移是 Python 字符位置，绑定文本 SHA-256。旧数据库文本没有原始页信息时页码保持未知，不由迁移或模型编造。模型不能提交可信页码/偏移。

服务端重新查找原文引用与经历 scope，保留字段路径、原值、归一值、页/段/字符位置和风险原因。不存在的引用、Java/JavaScript 等词边界不符、证据超出所属经历、重复或重叠 scope、否定/计划/未知、冲突及疑似指令文本保守标记 unsupported/needs_review。有界检查不足时不强判。

这些检查只能提供字面支持及有限归属防护，不能证明完整语义、真实履历或成果因果关系。宽泛经历段落、真实复杂排版、隐含否定和别名长尾仍需用户审核；本批不自建复杂 NLU。候选 UI 显示原文、位置、字段归属和风险，确认后的来源持续标明 Mock 测试结果。

## 4. 持久运行与一致性

0005 增加 `resume_extraction_runs`、文档 text_layout、候选运行/追溯元数据及正式条目测试来源元数据。运行归属当前用户，queued/running/succeeded/failed/cancelled 与候选审核状态分开。表不保存完整 Prompt、模型 JSON、API Key 或原始异常；简历硬删后仅保留同 owner 可查的最小运行终态。

运行采用数据库领取、执行令牌、租约、资源版本与 revision；Provider 调用期间释放写锁，提交结果时再次检查归属、资源状态/版本、运行状态/令牌和超时。候选与 succeeded 由同一个事务提交；不会先显示成功再遗漏候选。请求幂等及同简历/版本/抽取版本去重防止重复运行/候选，重试不清零调用预算。

默认 Mock 关闭，仅 development 显式开启；本地执行器查询数据库中的持久 queued，重启不依赖内存消息。失效 running 在查询/恢复时进入可查询失败，不永久运行；取消或删除使令牌失效，迟到输出丢弃。删除意图同事务清正文、布局、候选、原文来源与相关运行状态，用户已确认的正式值仍按 S2-1 规则保留。

本批复用 SQLite 事务与有界 Mock 执行，没有新增 Redis/Celery 或依赖。该方案支持本机合成验证；真实付费网络请求的超时隔离、费用不确定性、生产容量与 PostgreSQL 行锁需 S2-2B/上线批次另行验证。

默认租约 30 秒（配置 1～60），最多两次执行、合计四次 Provider 调用；输入最多 50,000 字符、响应最多 100,000 字符，Schema 修复每次最多两次调用，与持久总预算共享。每用户最多三个 queued/running、全局二十个；dispatcher 每轮最多三条逐条执行，CLI limit 1～100。数据库强制 running 同时具有 token/lease，其他状态两者为空。超时是持久状态/写入围栏，当前固定 Mock 不联网；不能硬杀任意阻塞线程的生产 runner 尚未实现。

## 5. API 与测试界面

| 方法及 `/api/v1` 路径 | 用途 |
|---|---|
| GET /resumes/{id}/extraction | 开关、资源版本及最近运行，不返回全文 |
| POST /resumes/{id}/extraction-runs | mode=mock、幂等键与资源版本；持久 queued |
| GET /extraction-runs/{id} | 当前用户运行状态及有界调用/尝试计数 |
| GET /extraction-runs/{id}/candidates | 仅该用户、该运行的候选 |
| POST /extraction-runs/{id}/cancel | revision 条件取消 |
| POST /extraction-runs/{id}/retry | revision、状态、资源版本与剩余额度校验 |
| POST /profile/candidates/{id}/review | 复用既有用户确认/修订/拒绝 |

所有资源按 Cookie 服务端身份授权，写操作检查 CSRF/Origin；请求不接受 user_id、模型名、任意供应商 URL、Key 或自造证据状态。前端由真实 OpenAPI 生成类型，代理只追加明确路径/方法白名单。

测试 UI 另需显式服务端开关，只在简历详情追加最小面板；自动查询有次数上限、离开取消。默认正式界面没有真实 AI/Mock 触发按钮。Mock 只接受登记合成样本，未登记材料安全失败，不从真实简历制造固定候选。

## 6. 自动化、评测与浏览器结果

| 验证入口 | 最终实际结果 |
|---|---|
| backend/.venv/Scripts/python.exe -B -m unittest discover -s tests -q | **222/222**，84.828 秒，0 失败/0 跳过；原 167 + 新 55（29 V2/候选、20 工作流、6 迁移） |
| tests/http_file_lifecycle_smoke.py | 真实 localhost HTTP 通过（6.950 秒），旧文件生命周期继续有效 |
| npm run test | **39/39** Vitest，10.14 秒，原 30 保留 |
| npm run test:e2e | **16/16** 完整 Chromium，1.2 分钟，原 12 保留；样式/文案后受影响两场 2/2（21.4 秒），截图修正后合成闭环 1/1（13.0 秒） |
| npm run api:check、typecheck、lint、build | 均通过；真实 OpenAPI/TypeScript 无漂移，生产构建仅本机构建验证 |
| 新工作流/全部迁移专项 | **20/20**（19.810 秒）；**30/30** 全部迁移（12.788 秒，含新 6），并纳入最终全量 |
| evaluate_resume_extraction_v2.py --output backend/evaluations/s2-2a-synthetic-report.json | exit 0，11 个固定合成案例，0 付费/网络调用；独立 gold 对照见下文 |
| git diff --check、范围与清理检查 | exit 0；旧迁移/Provider/依赖锁无差异；E2E 临时进程/数据库清理，3001/8001 监听数 0 |

仓库没有配置独立 Python formatter/linter/typechecker，本批使用已有 unittest 与差异检查；未虚报不存在的检查。原 Starlette/httpx 弃用提示仍存在，未为本批无关升级依赖。

### 关键安全和恢复实证

- 同一任务双线程执行只领取一次/调用一次，最终一批候选；同简历版本用不同请求键并发创建只有一个任务。相同请求键/任务复投、模型重复条目与 confidence 变化不产生重复候选；已成功运行不能追加新批次。
- 真实双用户/API 401/404/CSRF/Origin、伪造归属和运行关联、旧 revision/资源版本、默认关闭及非 development 强开均拒绝；用户 B 无法读写 A 的运行/候选。
- 运行中取消/删除后释放阻塞的测试 Provider，观察真实执行结束再查询，候选为零。删除也检查 PDF/正文/布局和候选引用清除；已确认值继续保留测试标记。旧 token、运行中正文变化、成功后文本/布局/文件版本变化都无法确认/写回旧证据。
- 无效 JSON/缺字段/严格 Schema、未知 Mock 材料、调用额度和尝试上限安全失败；高 confidence、不存在证据、否定/计划、正反冲突、长句/零宽、归属交叉与提示注入不越过审核。
- 候选 flush 后异常回滚候选及画像初始化；映射期间租约过期，最终提交检查失败并整体回滚；真实成功提交后丢 acknowledgement 仍以数据库 succeeded 为准，不重复写入或标失败。
- 独立 Python 子进程领取后执行 `os._exit`，数据库真实留下 running；租约到期后另一个有限 CLI `--recover-only` 恢复为 failed/lease_expired，旧令牌不能提交，剩余额度允许显式 retry。没有用内存状态伪造进程重启。

### 字段评测及错误案例

报告为 [s2-2a-synthetic-report.json](../backend/evaluations/s2-2a-synthetic-report.json)，可按 README 命令重新生成。gold 与 Provider 响应分开维护；输出包括每个 case 的字段路径/错误、风险及定位统计。

- **非空期望字段：146 正确、1 错误、1 缺失；null 正确 41。** 包含 null 的总比较为 187 正确、1 错误、2 缺失，另有 4 个多余字段。数字是固定变体的比较次数，不是独立真实简历样本上的精确率/召回率。
- **原文定位：161/163 个提议叶子具有逐字一致位置（98.77%）。** 两个缺口属于故意错误引用；“找到引文”不能证明提议事实语义正确或经历归属正确。
- `similar_skill_alias` 的伪 Java 提议不能由 JavaScript 支持，标 unsupported；`nonexistent_evidence_high_confidence` 的 Rust 即使 confidence=0.99 也没有证据，仍阻止确认。
- `cross_entry_attribution` 故意将另一项目工作内容放入当前项目，字段报告显示错误并阻止候选。正常 gold 事实本可支持，而此故障输出证据错误；这种保守阻止计 1，不能称正常误阻止。
- 正常合成案例预期 supported 却被阻止为 0，护栏失败为 0；缺字段/非法 Schema/无效 JSON 各两次后结构化失败。否定、计划、缺毕业、多段日期、重复与注入有单独结果。

这些成绩只证明固定响应的结构映射、位置与护栏；没有测试真实 DeepSeek 的召回/语义质量，没有宣称真实 AI 达标。

### 浏览器最终证明与视觉范围

Chromium 使用临时 SQLite 经 0005、真实 FastAPI/Next 和登记合成中文 PDF。页面主动创建得到 queued/最终 succeeded，生成 **7 个 pending 测试候选**；任务查询和候选入库后四类正式集合仍为空。资料页显示原文第 1 页/第 3 段/字符位置，Java 否定与 Go 学习计划的原值确认按钮禁用。用户主动确认 Python 后，唯一正式技能成为 Python，其他六项仍 pending；来源继续显示 Mock 测试结果，刷新后保持。

后端还通过同一 review 接口确认教育、修订被阻止技能、拒绝学习计划；正式条目来源分别正确。前端测试模式不另建画像/确认通道。默认无入口由组件测试验证，真实浏览器全链在显式隔离 Mock UI 模式执行；未伪称已经在正式真实 AI 模式运行。

新增截图均为合成资料，Git 忽略：任务 [1440](../frontend/artifacts/screenshots/extraction-task-1440.png)、[768](../frontend/artifacts/screenshots/extraction-task-768.png)、[390](../frontend/artifacts/screenshots/extraction-task-390.png) 及 [候选/定位](../frontend/artifacts/screenshots/extraction-candidates-1440.png)。三宽任务无水平溢出；实际查看桌面/手机与候选截图，修复局部按钮边距、图标排列及截图滚动位置后复验。保留原 UI，未做全工作台改版、Firefox/WebKit、真实用户研究或完整无障碍认证。

## 7. 迁移兼容性与个人数据

0001～0004 文件保持不变。0005 向前加列与新表，不认领旧 NULL-owner，不重建简历父表，不把旧派生数据变正式事实，不伪造旧页坐标。新库及旧数据快照、外键/检查/唯一约束、metadata 漂移和拒绝有损降级需自动验证；仅迁移临时测试库。

页段仅为原文回指，V2 叶子与必要引用仅为审核/可追溯映射，运行计数/版本仅为幂等和恢复。不新增联系方式收集，元数据/正文/密钥不进生产日志。来源简历删除清定位和原文；正式值保留测试来源标记。完整账户导出/注销、实际保留期限与备份删除重放仍待单独实现；硬删/unlink 不代表 SQLite 空闲页或备份安全擦除。

## 8. S2-2B 剩余条件与停止点

- 单独授权真实 DeepSeek 接入及费用；本批零付费调用，未收集/保存用户 Key。
- 批准供应商数据处理、区域、保留/训练配置；实现最小发送预览和明确调用授权。
- 实现会话 BYOK 的安全接收、期限、退出撤销与失效处理，禁止开发者 Key 回退。
- 通过独立迁移/版本化契约扩展当前 Mock-only 的运行 CHECK、Provider/测试来源元数据，不能只切开关就把 Mock 结果改称真实模型。
- 验证真实请求/SDK重试共享预算、超时隔离、未知费用结果、取消/删除与会话失效边界。
- 在独立标注、多样且获授权样本上测真实字段精确率/召回率、证据与归属、人工修正成本；Mock 成绩不能替代真实质量门槛。
- PostgreSQL、正式容量、PDF 原生解析隔离、HTTPS/ACL、账户生命周期与备份政策仍属后续。

完成 S2-2A 后停止，不自动进入 S2-2B。
