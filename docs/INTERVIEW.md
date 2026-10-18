# OfferPilot 面试讲解材料

本材料依据 S1～S5-2 的代码、迁移和验收记录整理，面向 GitHub 作品展示和技术面试。项目当前定位是可在本机复现的求职管理作品，不以公网部署、商业运营或真实用户规模作为成果。

**使用前先核对个人贡献。** 仓库包含 AI 编码协作规则及分批开发记录，但提交记录不能证明某个人独立完成了全部设计和代码。下面提供的是可以结合实际讲解的技术内容。简历和面试中应说明自己实际负责的需求决策、方案取舍、实现、审查和验证，以及 AI 辅助的部分；没有亲自完成或无法解释的工作不使用“独立设计并实现”等表述。第 10 节介绍稿也应按本人经历调整。

## 1. 项目背景与开发职责

求职过程通常分散在 PDF 简历、招聘 JD、表格和日历里。OfferPilot 把它们组织为“简历 → 待确认候选 → 正式画像 → 已审核岗位条件 → 规则匹配 → 申请与下一步行动”的流程。重点是能看清判断依据、纠正资料并持续跟踪申请。

可以演示的能力包括邀请登录、文本 PDF 管理、候选证据审核、四组正式画像、私有职位与 JD 审核、确定性匹配、投递时间线与工作台、本人 ZIP 导出和账户注销。岗位选择、事实确认和实际申请提交由用户决定。项目没有自动海投、浏览器代填、通知派发或在线招聘站抓取。

介绍职责时可使用这一填写框架，**替换方括号后再用于简历或面试**：

> 我在 OfferPilot 中主要负责［本人实际负责的范围］，使用 AI 编码助手协作完成了［实际协作范围］。我能够结合代码说明［本人掌握的技术问题］，并通过［本人实际运行或审查的验证］确认行为。项目以本地可复现为交付目标，没有真实用户运营或生产部署经历。

证据入口：[PRD](PRD.md)、[开发计划](PLAN.md)、各阶段 `S*-acceptance.md`、[当前实现导读](ARCHITECTURE_GUIDE.md)。

## 2. 为什么采用 FastAPI + Next.js

后端已有 Python PDF 处理与抽取原型，沿用 Python 可以复用解析、Pydantic Schema 和模型 SDK。FastAPI 负责 HTTP 契约与身份依赖，SQLAlchemy/Alembic 负责持久化和迁移；领域规则放在服务模块，避免路由承担业务逻辑。此处主要是模块化单体，并非微服务系统。

前端使用 Next.js App Router、React、TypeScript，配合 Tailwind、Radix 和 Lucide 组织中文响应式界面。浏览器通过同源 API 代理调用 FastAPI；代理限制路径、方法、Cookie 和响应大小，后端继续执行认证与授权。前端由 OpenAPI 生成 `schema.d.ts`，契约检查比对后端实际 Schema、快照和生成类型，减少两端手写结构漂移。

需要承认的代价是本地需要同时启动 Python API 和 Node Web 进程，代理还需正确处理 Origin、CSRF、取消和超时。这里没有用 Next.js 直接访问数据库，也没有把前端类型检查当作后端运行时校验。

代码入口：[`main.py`](../backend/src/offerpilot_api/main.py)、[`api/routes`](../backend/src/offerpilot_api/api/routes)、[`api.ts`](../frontend/src/lib/api.ts)、[`proxy.ts`](../frontend/src/lib/proxy.ts)、[`check-contract.mjs`](../frontend/scripts/check-contract.mjs)。

## 3. AI 抽取和证据验证原理

抽取的第一步是本地解析文本 PDF，记录文本及页段布局。真实模型模式下，用户配置会话 BYOK、查看脱敏发送预览，再单独确认数据外传和费用。发送的是批准的文本，原 PDF 不发给模型；遮蔽只是尽量减少个人信息，不能承诺完全匿名。

模型只提出候选。`StudentProfileExtractionV2` 要求教育、技能、项目、实习/工作四组结构，各字段包含值、引文、断言和模型自报置信度；严格类型、长度/数量限制和 `extra="forbid"` 拒绝不符合契约的输出。页码和偏移由服务端定位，不信任模型自报坐标。

服务端继续核对原文是否存在、值能否由引文支持、证据是否属于相应经历范围，并用有界词法规则识别否定、计划、冲突、范围重叠等风险。通过这些检查也不代表履历真实或语义完全正确。所有结果先进入 `pending` 候选池，用户确认或修订后才形成正式画像；模型置信度不触发自动确认，缺乏支持的候选不能直接原值确认。

`LLMProvider` 是供应商中立接口，DeepSeek 细节在 Adapter 内。真实任务限定一次调用，不进行收费修复或自动重试；超时/取消可能已计费，系统保留“费用结果未知”的状态，不能把网络异常等同于没花钱。

**能引用的模型证据：** [2026-10-14 单样本报告](S2-2B-single-real-evaluation-2026-10-14.md)记录一次真实 DeepSeek 正常合成 PDF 请求，5 项候选全部待确认，19/19 非空字段、21/21 引文定位和19/19非技能范围归属通过该样本核对。它不是多样本准确率，也未验证其余四份真实模型样本。日常 Mock/故障桩验证的是流程和边界，不能冒充模型效果。

代码入口：[`extraction_schema.py`](../backend/src/offerpilot_api/resume_ingestion/extraction_schema.py)、[`extractor_v2.py`](../backend/src/offerpilot_api/resume_ingestion/extractor_v2.py)、[`evidence.py`](../backend/src/offerpilot_api/resume_ingestion/evidence.py)、[`profile/services.py`](../backend/src/offerpilot_api/profile/services.py)、[`providers/deepseek.py`](../backend/src/offerpilot_api/agent_runtime/providers/deepseek.py)。

## 4. 为什么匹配采用确定性规则

求职匹配涉及学历、届别、技能和经历等会影响行动的条件。只给一个模型分数难以解释，也容易把“简历没有写”误判为“不具备”。项目采用纯规则函数读取正式画像和当前 JD 已审核条件，逐项输出“明确满足、部分满足、未发现证据、明确不满足”，同时保留双方证据。

例如“本科在读”不能当作已取得本科学历；预计毕业不能当作实际毕业；泛称“相关专业”不能自动扩展专业范围；JavaScript 不能因为包含 Java 字样就满足 Java；不同项目的零散事实不能拼成同一段经验。条件语义不清、未审核或证据缺失时保持未知。

综合建议只有“存在明确硬性条件冲突”“建议先核实关键条件”“可以进一步考虑”。优先条件和个人偏好分开展示，不作为自动拒绝条件。结果没有录用概率，也不替用户隐藏、拒绝或投递岗位。

匹配快照保存画像 revision、岗位 revision、JD 版本和规则版本。读取时与当前依据比较，动态提示失效；更新资料后重算产生新依据的结果，历史保留。同依据重复计算由唯一约束和回执去重。代价是规则覆盖范围有限，需要明确维护别名和资格语义，不能声称通用自然语言理解。

代码入口：[`matching.py`](../backend/src/offerpilot_api/job_intelligence/matching.py)、[`match_schemas.py`](../backend/src/offerpilot_api/job_intelligence/match_schemas.py)、[`match_services.py`](../backend/src/offerpilot_api/job_intelligence/match_services.py)、[S3-2 验收](S3-2-acceptance.md)。

## 5. 如何保证用户数据隔离

密码使用 Argon2 散列；随机会话凭据放在 HttpOnly Cookie，数据库保存摘要。服务端从有效会话解析用户，领域查询同时约束 owner 和资源 ID，不接受客户端指定 `user_id`。写请求还校验可信 Origin、Fetch Metadata 和 CSRF。外人 ID 与不存在 ID 使用安全错误响应，不泄露对象内容。

私有 PDF 和导出 ZIP 不通过静态公开目录提供下载，而是经过本人权限、生命周期及完整性检查的 API。Next.js 代理只能转发允许的接口，不能接受任意目标 URL；保存的岗位来源链接也不会触发服务器访问。前端不持久保存 CSRF 或 BYOK Key，个人 API 禁止缓存。

账户注销先记录主数据库之外的删除意图，再撤销全部会话、Key 引用和任务，随后分批清理。认证业务写入、后台结果和注销共享用户外层锁与删除围栏，阻止旧请求或迟到结果重建已删资料。删除清单缺失或损坏时失败关闭；恢复旧备份必须挂接最新清单并重放删除。

这里是应用层对象授权及数据库约束，**没有实现 PostgreSQL RLS**。本地文件私有也不等于磁盘物理擦除；备份和供应商副本的保留边界需要单独说明。

代码入口：[`identity/security.py`](../backend/src/offerpilot_api/identity/security.py)、[`identity/dependencies.py`](../backend/src/offerpilot_api/identity/dependencies.py)、[`resume_ingestion/access.py`](../backend/src/offerpilot_api/resume_ingestion/access.py)、[`account_lifecycle/guards.py`](../backend/src/offerpilot_api/account_lifecycle/guards.py)、[`ledger.py`](../backend/src/offerpilot_api/account_lifecycle/ledger.py)。验证见 `tests/test_resume_ownership.py`、`tests/test_account_deletion.py` 和 `tests/postgres/test_account_lifecycle.py`。

## 6. 如何处理任务幂等与并发

持久任务不是内存队列里的回调。抽取任务表保存 `queued/running/succeeded/failed/cancelled`、执行令牌、租约、revision、输入版本、尝试和调用计数。请求键防重复创建，资源/抽取版本唯一约束防止换键重复同一任务；调用额度在发出请求前持久预占，异常后不清零账本。

Provider 调用期间不持数据库写锁。完成时重新核对 owner、账户/简历状态、输入版本、执行令牌和租约，将候选与 `succeeded` 一次提交。取消、简历删除、会话失效或旧版本结果会失去写入资格。租约过期后可以查询明确失败；Mock 在剩余额度内支持显式有限重试，真实付费任务不自动重放。

并发编辑用 revision 比较并交换，旧版本返回 409，前端保留本地输入供核对。申请当前值、分类事件和幂等回执在同一事务提交；响应丢失后用原请求键恢复，不重复追加事件。删除申请保留最小回执墓碑，阻止旧创建请求复活资源。

PostgreSQL 中不同简历的行锁不能保护全局准入总量，因此任务准入短事务使用固定服务端键的 advisory lock；首次创建唯一画像的竞争使用 savepoint，避免唯一冲突破坏外层任务事务。文件和数据库无法组成单一 ACID 事务，原件清理另用持久操作记录、`.claim` 归属证明和有界补偿处理。

边界：目前是单 API 进程 lifespan 有限批次执行和恢复 CLI，没有 Redis/Celery，也没有跨进程真实 BYOK Vault。租约能阻断迟到写入，不等于可以强制终止任意阻塞中的第三方调用。

代码入口：[`extraction_runs.py`](../backend/src/offerpilot_api/agent_runtime/extraction_runs.py)、[`profile/services.py`](../backend/src/offerpilot_api/profile/services.py)、[`application_tracker/services.py`](../backend/src/offerpilot_api/application_tracker/services.py)、[`resume_ingestion/lifecycle.py`](../backend/src/offerpilot_api/resume_ingestion/lifecycle.py)。

## 7. PostgreSQL 与 SQLite 的区别和测试

SQLite 适合 development 轻量启动和大量临时测试，需逐连接开启外键；单写者特性会掩盖部分并发问题。PostgreSQL 适合验证真实多连接竞争、行锁、权限和超时。项目统一通过 Settings 与 `build_engine` 构造连接，应用和 Alembic 使用同一配置；非 development 禁止回退 SQLite。

PostgreSQL 使用独立迁移与业务角色，业务角色无 DDL/建库/角色管理权限；启动核查实际继承权限与对象所有权。每连接强制 UTC，并设置池等待、连接、语句、锁及空闲事务上限；查询参数和服务端错误语句尽量不进入日志。现有 JSON 列没有仅为换库而改成 JSONB。

测试不能只证明“同一套 ORM 在两库能跑”。仓库还验证新库迁移、0001～0010逐级合成历史升级、约束/元数据一致性、真实独立连接竞争、受限角色、文件补偿、旧备份删除重放以及真实 HTTP/Chromium 主流程。普通后端测试使用临时 SQLite，PostgreSQL 为显式启用的隔离测试；出现 opt-in 跳过时不能称为已验证 PostgreSQL。

历史 [S5-1](S5-1-acceptance.md) 与 [S5-2](S5-2-acceptance.md)报告的环境是 Windows 本机、Python 3.11、Docker 内 PostgreSQL 18.6。S5-2 记录前端 140 项及 PostgreSQL Chromium 36 项通过；后端全量和后续专项有分别记录，不能把重复执行累加成新覆盖率。本轮作品集交付的执行结果以新的验收记录为准。这些都是本机合成测试，不证明生产容量或用户效果。

代码入口：[`db/session.py`](../backend/src/offerpilot_api/db/session.py)、[`migrations`](../backend/migrations)、[`tests/postgres`](../tests/postgres)、[`scripts/postgres`](../scripts/postgres)。

## 8. 可以深入展开的技术问题

以下问题有实际代码和故障记录；面试时选择自己真正参与、能够现场解释的两项。

| 问题 | 方案和验证 | 需要承认的边界 |
|---|---|---|
| 引文存在，但来自另一段同名经历 | 服务端页段定位、经历 scope 和字段归属重验；多经历、否定/计划及冲突合成反例 | 词法护栏不能证明履历真实性，也不是完整语义验证 |
| PostgreSQL 不同简历同时提交，超出全局任务额度 | 准入计数和写入用事务 advisory lock，独立连接竞争测试 | 只在短准入事务内串行，不能跨模型请求占锁 |
| 两个来源首次同时创建同一正式画像 | 唯一约束配合 savepoint 保留外层 run/resume 事务；用户显式旧 revision 仍冲突 | 只对内部无显式版本路径做有限恢复，不能静默覆盖用户编辑 |
| 删除数据库记录成功，文件删除失败 | 持久删除意图、对象归属证明、失败状态和有界重试；真实 IO/提交故障注入 | 不扫描删除未知对象，证明损坏需要维护者处理 |
| 旧备份恢复了已经注销的账户 | 主库外最小删除清单、认证失败关闭、隔离恢复后重放；比较其他用户指纹和文件哈希 | 清单必须独立保存，丢失不能初始化为空绕过 |
| 导出旧执行失败补偿可能清掉新租约文件 | 清理前重取 User→任务锁并核对执行令牌；SQLite/PostgreSQL 可重复竞争测试 | ZIP 有字节/记录/时间额度，物理清理失败要明确呈现 |
| 职位列表页数变大，SQL 随条数增长 | 对当前页条件状态一次分组统计，5/30 行查询数均有本机观测 | 小规模本地样本不能外推生产吞吐或 SLA |

记录来源：[S2-2A](S2-2A-acceptance.md)、[S3-2](S3-2-acceptance.md)、[S5-1](S5-1-acceptance.md)、[S5-2](S5-2-acceptance.md)。

## 9. 当前局限与可改进方向

- 文本 PDF 限 10 MiB、50 页；不支持 DOCX、扫描件 OCR 和任意复杂版式。后续可先增加多样合成标注集，衡量误抽、归属错误和修正成本。
- 真实 DeepSeek 只完成一次正常合成样本验收。模型测试覆盖、供应商数据条款和版本变化仍有缺口；本地无 Key 时可手工完成非 AI 流程，CI 不依赖收费 API。
- 会话 BYOK 为单进程内存方案；真实调用不能跨独立 Worker 恢复。当前作品定位无需为此引入分布式密钥系统。
- 匹配规则是有界资格语义，不覆盖所有行业、专业和招聘措辞。可通过独立反例扩充规则，保留未知，不追求虚假的综合分。
- 未实现浏览器辅助、岗位抓取、定向简历重写、主动通知或自动申请。后续优先改善本地复现、可解释交互和有价值的错误路径测试。
- 本地数据删除有逻辑清理和恢复围栏，未验证生产存储 ACL、灾难恢复、物理擦除或第三方即时删除。没有生产运维或多节点高可用成果。
- 使用 PyMuPDF，公开分发需明确履行适用的 AGPL 或商业许可义务；不能用“作品集”定位替代许可审查。

## 10. 2～3 分钟项目介绍稿

> 下面这份稿件使用“在 AI 编码助手协作下开发和整理”描述协作方式。讲解者仍须按实际参与情况调整；本稿不证明个人独立完成全部功能。

OfferPilot 是一个在 AI 编码助手协作下开发和整理的求职管理项目，目前主要用于 GitHub 作品展示和本地演示。它关注的是一个很具体的问题：简历、岗位要求和投递进度通常分散在不同地方，求职者很难看清自己为什么匹配一个岗位，以及下一步应该做什么。

项目的流程是上传文本 PDF，把抽取内容放进待审核候选池；用户确认后形成正式画像，再录入并审核岗位条件，生成可解释匹配，最后手工管理申请、面试和下一步行动。没有模型 Key 也能手动录入、匹配和管理投递。

技术上，前端使用 Next.js 和 TypeScript，后端是 FastAPI 模块化单体，通过 OpenAPI 生成前端类型。数据库支持本地 PostgreSQL 和开发用 SQLite，原件和导出文件放在私有目录。

我会重点展示三个设计。第一，模型输出不是事实：结构必须通过版本化 Schema，服务端重新定位原文和经历范围，最后仍由用户确认。第二，匹配不用模型打总分，而是根据正式画像和已审核条件输出四种状态，尤其区分“没有证据”和“明确不满足”。第三，任务和文件清理有持久状态、幂等键、租约及版本围栏，取消或删除后的迟到结果不能重新写回。

验证方面，仓库有临时数据库测试、真实 PostgreSQL 多连接并发、前端组件测试和 Chromium 完整流程，也保留了失败修复记录。模型效果只做过一次正常合成简历的真实 DeepSeek 验收，不能因此宣称总体准确率。项目没有公网部署或真实用户运营；我希望通过这个作品说明自己对［本人真正掌握的设计和验证内容］的理解，并能现场结合代码解释取舍和限制。

## 建议现场演示顺序

按 [DEMO.md](DEMO.md) 先展示无 Key 的真实业务闭环，再选一个安全或并发问题讲解。候选审核若采用合成 Mock 测试模式，应当场说明测试身份；真实模型只展示注明来源的历史单样本报告，不在面试演示中临时发起付费请求。最后打开对应服务代码与反例测试，比只展示测试数量更有说服力。
