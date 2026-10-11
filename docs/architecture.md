# OfferPilot 系统架构与技术方案

**版本：** 0.4（保留目标设计，补充 S1-3 Web 边界）
**日期：** 2026-10-11

> **当前实施范围：** Phase 0～1-3 已有 FastAPI、配置、SQLAlchemy/SQLite、健康检查、三表与初始迁移、本机 PDF 上传/文本提取、四字段内存抽取核心与 Provider。S1-1 新增邀请注册、Argon2id、数据库会话、当前用户和简历父链对象授权；S1-2 新增私有本地 Storage Adapter、原件下载、文件/派生删除、清理记录与有限恢复 CLI。AI 抽取仍未接入 API/持久化。S1-3 已增加 Next.js 中文工作台与同源 API 代理。尚无 PostgreSQL、Redis、云对象存储、Worker、画像确认、岗位匹配或业务 Agent。下文保留后续目标设计，优先级以 PLAN/PRD/Roadmap 为准。

Phase 1-2 上传接口仅在 `development` 环境可用，默认上传目录为本地 `backend/uploads/`；仅接收不超过 10 MiB、50 页且有可选中文本的 PDF。PyMuPDF 采用 AGPL 或商业许可，正式发布前需确定适用许可方案。


### S1-3 已实现的 Web 边界

`frontend/src/app` 以 App Router 提供中文页面；`src/components` 封装任务 UI，`src/lib/api.ts` 使用真实 OpenAPI 生成的 `schema.d.ts`。`/api/v1/[...path]` 只代理当前 auth/me/resumes 白名单方法与路径，目标来自服务端环境配置；不接受客户端任意 URL、用户归属或数据库操作。FastAPI 仍负责密码、会话、CSRF、资源授权与所有文件领域规则。

代理保留原 Origin、Sec-Fetch-Site、X-CSRF-Token 和唯一会话 Cookie，只转发后端的 offerpilot_session Set-Cookie；no-store、限体积、55 秒请求上限，读取请求体与上游请求均可取消，没有写操作自动重试。后端与前端都显式配置可信 Web origin，不能把不可信来源改写为可信来源。浏览器仅内存保存 CSRF，刷新从 auth/csrf 恢复；会话秘密只在 HttpOnly Cookie。旧请求迟到不能覆盖新会话 CSRF 或触发新会话退出。

当前是客户端确认会话后显示工作台，API 由后端强制授权，页面不包含服务端预渲染的个人资料。文件状态和清理通过真实 lifecycle 接口读取；自动查询有次数/间隔上限，页面卸载终止请求。详见 [前端设计](../frontend/DESIGN.md) 与 [S1-3 验收](S1-3-acceptance.md)。本批没有 Worker、队列、AI 或新数据表；账户导出/注销仍单独后续验收。

## 1. 架构决策摘要

**待决策说明：** 下列 Next.js、PostgreSQL 具体版本、Celery/Redis、对象存储、部署容器和观测供应商均为原设计候选，未由本轮验证或安装。保持模块化单体；Worker 在 S2 长任务需要时再决定，Browser 独立 S7，DOCX/OCR 和通知首发后评估。MVP 岗位来源为粘贴/手动输入，不访问保存的 URL。具体容量、价格、数据期限与部署许可需相应批次验证。

- 采用**模块化单体**；独立异步 Worker 为后续有持久任务时的候选，早期不拆微服务，降低部署与调试成本。
- Web 使用 Next.js/TypeScript；API、解析、Agent 编排使用 Python/FastAPI，共享明确的 OpenAPI 契约。
- PostgreSQL 是业务事实的主存储；Redis 只承载任务队列/短期协调，不作为事实数据库。
- 简历原件放私有对象存储，数据库只保存对象键和元数据；敏感文本按需加密或控制访问。
- Agent 采用显式、可恢复的工作流状态机和窄权限工具；模型只负责提取、归纳、解释，不拥有申请提交权限。
- 第一阶段岗位匹配以可解释规则为主；向量搜索属于后续可选能力，不作为推荐正确性的唯一依据。

## 2. 系统上下文

```mermaid
flowchart LR
    U[学生] --> W[OfferPilot Web]
    W --> API[FastAPI 应用模块]
    API --> PG[(PostgreSQL)]
    API --> OBJ[(私有对象存储)]
    API --> Q[Redis 队列]
    Q --> WK[异步 Worker]
    WK --> LLM[LLM Provider Adapter]
    WK --> SRC[获准的岗位来源 / 用户提供的 JD]
    WK --> PG
    WK --> OBJ
    API --> B[受控 Browser Agent]
    B --> SITE[用户选择的企业招聘页面]
    WK --> NOTIFY[应用内通知 / 后续邮件或日历]
```

### 部署边界

- **Web：** Next.js 前端；服务端仅做会话校验、页面数据装配和 API 代理，不承载长时间 AI 任务。
- **API：** FastAPI 模块化单体，负责身份校验、授权、领域用例、数据访问和任务提交。
- **Worker：** 与 API 同一代码库，独立进程运行简历解析、JD 分析、匹配生成、提醒派发等队列任务。
- **Browser Runner：** 独立隔离的浏览器会话进程/容器；短生命周期、按用户创建、限制出站域名和资源，不能访问内部管理端点或云元数据地址。
- **PostgreSQL：** 用户、画像、岗位、申请、事件、运行状态和审计数据的主库。
- **对象存储：** 私有桶保存简历原件与必要的导出件，短时签名 URL，生命周期和删除任务可追踪。
- **Redis：** Worker broker/短期协调；不放简历正文、长期令牌或唯一业务状态。

## 3. 逻辑模块

### S1-2 已落地边界

HTTP 路由只处理会话/CSRF、Schema、附件头与安全错误；`services/resume.py` 复用原 PDF 规则，协调先入库上传记录、存储、文本解析、CAS 完成和失败补偿；`resume_ingestion/lifecycle.py` 承载 owner 过滤、删除意图、清理状态、下载完整性、幂等和迟到结果隔离；`storage.py` 只处理不透明对象键的受限 save/read/delete，拒绝路径穿越、符号链接/reparse 与创建证明冲突。不使用云 SDK 或进程内锁伪装跨进程可靠性。

数据库先持久化上传/删除记录，再执行文件操作。托管文件的 PDF/part/claim 共用私有根目录；claim 使用已入库简历 ID 证明本次创建，防止对象键碰撞和数据库更新失败时误删原有对象。处理中断由 `recover.py` 显式、有限批次恢复；证明损坏/归属冲突只安全失败并要求可信操作员核对，不扫描删除未知对象。已完成删除保留最小 owner/ID 幂等记录，正文/画像/事实/文件引用均清除。

当前受限文件 IO、解析和 CAS 持有 SQLite 写事务，使并发删/下载/上传按提交顺序一致；原件先完整读入受大小限制的内存并关句柄再响应。迟到处理只允许 active 文档完成，硬删父表 FK 兜底。该保守方案适用于本机验证；公开部署前再验证容量、超时/解析隔离与生产存储，不能将写锁或本机恢复 CLI 当成已交付 Worker/生产 SLA。优先复用这条领域状态边界，未来 Adapter/Worker 替换不得绕过 owner 和状态检查。

### S1-1 已落地边界

`identity` 管理密码、邀请、会话与限流；`api/routes/auth.py` 只负责 HTTP；`identity/dependencies.py` 统一从 Cookie/数据库解析用户并验证 CSRF。没有客户端可信 user_id 或无状态 JWT；可撤销 opaque 会话适合当前单体，复用数据库而不增加 Redis。成熟 `argon2-cffi` 使用 Argon2id 默认参数，不自行实现密码算法；增加了默认 12～128 字符和合法 UTF-8 输入校验。

`resume_ingestion/access.py` 在服务端 owner 条件下读取/修改元数据；画像/事实经简历父链查询。`services/resume.py` 新上传强制 UUID owner。列表/详情响应显式限定字段，授权先于写入；PATCH 仅改显示名，保留原件存储路径。旧上传兼容路径同样需会话与 CSRF，development 限制保留。

服务器保存会话/邀请 SHA-256 摘要，不保存原 token。会话固定期限、登录轮换、退出撤销、过期或账户停用拒绝；Cookie HttpOnly/SameSite=Lax，非 development 强制 Secure。CSRF 为会话秘密的用途专属 HMAC，每次修改验证；登录/注册 JSON + Origin/Fetch Metadata 保护。限流用数据库条件更新/唯一约束，按账号和实际连接 IP 限制，有界窗口和 429；部署前须验证代理转发头信任，不能相信任意客户端 IP 头。

错误、验证失败与 SQL 参数均脱敏；OpenAPI 显式声明安全错误 Schema，所有个人 API 返回 no-store。日志不打印请求体、Cookie、密码、邀请或个人字段；管理员 CLI 唯一例外是按授权向本机终端输出一次邀请秘密，不作为服务日志。暂无找回密码、邮箱验证、会话管理界面、账户删除或安全审计事件表；不能据 S1-1 宣称完成生产认证体系。

| 模块 | 职责 | 主要边界 |
|---|---|---|
| Identity & Access | 登录会话、用户租户上下文、权限校验 | 所有读写必须从服务端用户身份派生 user_id |
| Profile | 画像偏好、候选事实、确认状态和来源证据 | AI 抽取是候选值；确认由用户执行 |
| Resume Ingestion | 文件验证、文本抽取、分段、解析运行状态 | 文件不可信；MIME、扩展名、大小和恶意内容检查 |
| Job Catalog | 来源连接器、规范化岗位、JD 快照和过期状态 | 仅读取允许的公开/授权来源或用户提供内容 |
| Job Intelligence | JD 提取、匹配、解释、定向建议 | 输出必须结构化并绑定证据；规则可复核 |
| Application Tracker | 申请状态、材料版本、事件和提醒 | 状态变更记录事件；提醒可幂等重试 |
| Browser Assist | 表单识别、字段映射、用户审核、暂停/恢复 | 最小权限；没有最终提交工具 |
| Agent Runtime | 有界工作流、模型适配、工具授权、审计 | 状态持久化、最大步数、超时和预算上限 |
| Notification | 应用内通知及后续渠道发送 | 用户控制渠道与频率，发送结果可观测 |

模块以领域服务/用例调用，不互相直接读写任意表。外部模型、浏览器和招聘来源均经 Adapter/Connector 接口接入。

## 4. 关键数据流

### 4.1 简历到画像

1. API 校验用户、大小、格式和上传授权，写入私有对象存储。
2. 创建 `resume` 与解析运行记录，推送异步任务并返回运行 ID。
3. Worker 提取 PDF/DOCX 文本；如扫描件无可读文本，提示用户提供可检索版本，OCR 可在后续开放。
4. 解析器按版本化 Schema 生成候选事实，每个字段附原文证据、来源页/段、置信度。
5. 将候选字段展示给用户。用户确认/编辑后写入当前画像事实；原始抽取及用户修订可追溯。

### 4.2 岗位到推荐

1. 来源 Connector 或用户输入产生岗位快照，保存来源、抓取时间和内容指纹。
2. JD 分析器提取职位条件并标记原文证据和未知项。
3. 规则引擎先判断明确硬性门槛，再计算技能/经历/偏好维度；缺失信息为“未知”，不能直接判为不匹配。
4. LLM 将规则结果和证据改写为易读解释，不得改写规则判定或补造履历事实。
5. 用户反馈进入质量数据集；不会未经评估自动改变权重。

### 4.3 网申辅助

1. 用户从某条申请记录显式开启会话，确认目标域名和数据使用范围。
2. 用户在受控浏览器自行完成登录；凭据和会话存储限制在短时隔离环境中。
3. Browser Runner 读取当前页面可访问的表单结构，按已确认字段映射建议值；每个填充值保留资料来源。
4. 未知/敏感字段、页面变化、声明或异常挑战触发暂停，交还用户。
5. 用户逐页复核和操作页面。Agent 不提供“提交申请”工具，也不自动确认同意声明。
6. 会话结束后清理浏览器配置文件和短期页面数据，审计日志只记录必要事件，不记录字段明文。

## 5. AI / Agent 安全和质量边界

- 外部 JD、网页、简历中的文本都是**不可信数据**，可能包含提示注入；不作为系统指令，不因此新增工具权限或改变工作流。
- 工具调用采用固定 JSON Schema、参数校验、用户/资源授权、域名白名单、超时、步数上限和成本预算。
- LLM 仅处理当前任务所需的最少上下文；Prompt、模型、Schema 与输出版本化。
- 事实型字段保存证据；无证据或低置信字段必须标为未知/待确认。结构化输出验证失败时有限次重试，之后失败关闭并展示错误。
- 匹配分数按可解释的规则计算，LLM 不单独决定硬性条件、是否投递或申请结果。
- Browser Agent 的高风险动作通过权限设计直接禁用，而不是依赖提示词要求模型“不要提交”。
- 重要操作写入审计事件；生产可观测性默认脱敏，禁止将整份简历、Cookie、密码或申请答案写入普通日志。

## 6. 技术选型

| 层 | 建议 | 采用原因 / 说明 |
|---|---|---|
| Web | Next.js App Router、React、TypeScript | 面向仪表盘与多步审核页面；类型约束和前后端契约清楚。按官方当前文档在项目初始化时复核 Node 与框架支持范围。 |
| API | Python、FastAPI、Pydantic | Python 文档解析和 AI 生态；自动生成 OpenAPI；适合异步 I/O 与类型化输入输出。同步/异步处理遵循所调用库的 I/O 模型。 |
| ORM / Migration | SQLAlchemy 2.x、Alembic | 显式事务与迁移历史；版本在实现启动时锁定。 |
| 主库 | PostgreSQL 18（或托管服务当前支持的稳定版） | 关系型事务、JSONB 和全文检索可支撑首版主数据；首版不引入独立向量数据库。 |
| 队列 | Celery + Redis | 将文件解析、模型调用、提醒等长任务移出 HTTP 请求；任务状态仍落数据库，任务须幂等。若团队已有统一任务平台优先评估复用。 |
| 文件 | S3 兼容的私有对象存储 | 简历原件与数据库分离；私有访问、生命周期策略、删除补偿任务。 |
| 文件抽取 | PyMuPDF（PDF）、python-docx（DOCX）；OCR 后续评估 | 先支持文本型文件，扫描件明确提示，降低 MVP 解析不确定性。 |
| Browser | Playwright（隔离 runner，优先语义化 locator） | 可观察页面并操作浏览器；只启用辅助填写所需 API。隔离、安全边界和站点许可由应用层实现。 |
| AI | 通过 `LLMProvider` 接口接入支持结构化输出/工具调用的模型 | 可更换供应商；实现前按数据处理条款、区域、保留策略、结构化输出能力和成本做评估。 |
| 检索 | PostgreSQL 全文检索；pgvector 按离线评测再启用 | MVP 先验证规则匹配。向量相似度可补充召回，不能替代显式条件与解释证据。 |
| 观测 | OpenTelemetry 兼容追踪 + 结构化脱敏日志 + 错误监控 | 关联任务 ID、运行状态和耗时；禁止记录敏感正文和凭据。 |
| 部署 | Docker 化 Web/API/Worker/Browser Runner；托管 PostgreSQL/Redis/对象存储 | MVP 采用少量进程/容器，环境配置分离；正式部署需选定云区域、备份与密钥管理。 |

### 技术依据（官方或项目主仓文档，查阅日期 2026-09-25）

- [Next.js App Router 文档](https://nextjs.org/docs/app) 与 [Next.js 安装及系统要求](https://nextjs.org/docs/app/getting-started/installation)
- [FastAPI 并发与 async 文档](https://fastapi.tiangolo.com/async/)
- [PostgreSQL 文档](https://www.postgresql.org/docs/)
- [Celery 任务队列介绍](https://docs.celeryq.dev/en/stable/getting-started/introduction.html)
- [Playwright Locators](https://playwright.dev/docs/locators) 与 [Playwright Actionability / Auto-waiting](https://playwright.dev/docs/actionability)
- [pgvector 项目文档](https://github.com/pgvector/pgvector)

版本、许可、托管支持和安全公告在实际开工时重新确认；本方案不锁定未验证的具体补丁版本。

## 7. 运行与部署建议

- 环境至少划分 local、staging、production；各自独立数据库、对象存储桶和模型凭据。
- API、Worker 与 Browser Runner 用不同服务身份和最小权限；Browser Runner 不读取生产数据库凭据。
- 数据库连接使用 TLS 和受限网络；对象存储禁止公开访问；密钥进入 Secret Manager，不写入仓库。
- 数据库备份加密并定期验证恢复；保留策略和用户删除任务覆盖主库、对象存储、日志及供应商侧数据。
- 管理任务提供可查询状态与失败原因；人工重跑具备幂等保护。
