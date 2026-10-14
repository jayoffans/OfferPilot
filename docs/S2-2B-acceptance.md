# S2-2B 真实 DeepSeek 集成与安全 BYOK 验收

日期：2026-10-12（Asia/Shanghai）。本批完成允许范围的本地开发与免费验证；**真实调用待验收，不能宣称 S2-2B 全部完成或可公开上线。真实 DeepSeek 请求 0 次、付费调用 0 次、本轮实际费用 0 元。**

> 本文保留 S2-2B 代码开发阶段的历史基线。2026-10-12～14 追加的官方兼容性修复、五份合成白名单、加强后的收费门槛与免费回归以 [正式模型验收准备](S2-2B-readiness-review.md) 和 [当前执行手册](S2-2B-real-evaluation.md) 为准；下文六样本旧入口说明不再作为当前执行命令。真实调用仍为 0。

## 1. 基线与变更

开始前审查 PLAN、S2-2A 验收、README、PRD、architecture、database、roadmap、Adapter、V2/证据、持久任务、画像、前端、0001～0005 与相关测试。实际分支为 `feature/resume-agent`；原 S2-2A 有效工作保存为本地检查点 `78aecf8756b6ac1f1f22650eac12d3f6402693a6`（55 文件）。基线后端 222/222，81.874 秒。没有覆盖用户工作或推送。

复用既有上传、Schema V2、服务端原文定位、调用前持久计数、领取令牌/租约、候选原子入库和 S2-1 review/revision。没有第二套正式画像或重建抽取系统。

| 文件范围 | 本批变化 |
|---|---|
| agent_runtime/byok.py；api/routes/byok.py | 限时会话 Key、独立预览、授权快照、失效恢复 |
| agent_runtime/extraction_runs.py、extraction_schemas.py | DeepSeek 模式、一次调用预算、Key 引用、费用未知、usage、迟到围栏 |
| providers/deepseek.py；llm_provider.py | 复用官方 SDK Adapter；安全错误类别、usage、响应时间/大小限制、无 SDK 重试/重定向/环境代理 |
| resume_ingestion/extractor_v2.py、evidence.py；profile/schemas.py、services.py | 真实模式一次解析；脱敏模型输入与原件证据分开；模型/运行来源延续到用户确认值 |
| db/models.py；migrations/versions/0006_deepseek_byok.py | 追加授权、会话引用和计量列；扩展 Mock-only CHECK；保留旧候选运行关联 |
| identity/services.py；api/routes/auth.py；main.py；core/config.py；backend/.env.example | 退出/登录轮换撤销、执行器/Vault 装配、开发开关、生产关闭 |
| backend/pyproject.toml、uv.lock | 增加 cryptography，锁定 50.0.2；Fernet 内存加密，无 Redis/新 Agent 框架 |
| frontend/components/ai-resume-panel.tsx、profile-page、profile-editor、candidate-extraction-details、globals.css | 中文 Key/选简历/授权/状态界面与真实来源；沿用设计系统和审核 |
| frontend/src/lib/api.ts、proxy.ts、schema.d.ts；openapi.json | 生成契约与明确路径/方法白名单 |
| tests/test_byok_security.py、test_deepseek_paid_boundaries.py、test_byok_migration.py、test_deepseek_evaluation_quality.py | 会话/授权/并发/故障/迁移免费验证 |
| 既有迁移测试、test_resume_extraction.py；前端组件/API边界测试；e2e/byok.spec.ts、隔离 launcher | head 升级、禁用旧收费 CLI、保留回归、新 Chromium 场景 |
| backend/evaluations/evaluate_deepseek_v2.py；README、PLAN 和相关文档 | 显式授权评测入口、适用政策、执行说明与阻塞项 |

0001～0005 保持原文件。仅迁移测试临时库；未迁移实际开发或生产数据库，未部署、未推送，未开发 S3 或 Browser Agent。本批 S2-2B 变更留工作区供审查。

## 2. Provider 与数据验证

官方 `https://api.deepseek.com/chat/completions`，沿用已锁定 `openai` SDK；固定请求 `deepseek-flash`，`thinking.type=disabled`、`response_format.type=json_object`、`stream=false`、`max_tokens=4096`。不允许客户端提供模型、任意 URL 或代理。SDK `max_retries=0`；HTTP transport 不重试、不跟随重定向、不读取环境代理，TLS 验证保留。

2026-10-12 官方表中 flash 对应 DeepSeek-V4.1-Flash，支持 1M 上下文与最大 384K 输出；本产品采用更小限制。模型别名可能更新，运行保存请求模型及 Provider/Prompt/Schema/授权版本；预览记录脱敏版本，与本批授权版本绑定。不能把别名当不可变权重版本。资料：[模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[Chat Completions 参数](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)。

JSON 模式只提供格式约束。本地仍执行严格 V2、叶子结构、字段归属、原文引用、否定/计划/冲突和映射校验。真实模式 `max_attempts=1`，无效 JSON/Schema、截断、空响应及空候选均安全失败，不收费修复。模型只能提出 pending；用户主动 confirm/modify/reject 才影响正式画像。Mock 继续独立配置、固定合成输入及永久测试标签。

单次 I/O timeout 10 秒，响应读取 deadline 40 秒，每响应最多 1,000,000 字节；超出抛安全错误。40 秒是读取边界，单个在途 I/O 仍受其超时，不能解释成可硬杀任何 OS/DNS 阻塞线程的绝对 SLA。运行租约 60 秒独立阻断迟到提交；真实网络时延和系统级故障仍待授权验证。

## 3. 会话 BYOK

采用**单 API 进程内存 Vault**，避免本批引入未经验证的跨进程密钥共享。当前同进程执行器只凭 user/session/key_reference 解析临时 Key；有限 CLI 只处理 Mock。真实任务不能通过独立 CLI 或重启后的空 Vault 取 Key，也没有开发者 Key 回退。

- Key 为 SecretStr 请求，Fernet 密文只在加锁、有界内存中保存；加密主 Key 每进程随机生成，不落磁盘/环境/业务数据库。默认 TTL 3600 秒，配置 60～3600 秒，同时不得超过登录会话期限。退出/撤销/换 Key 清密文和预览；定时/读取清到期内容，正常关机清 Vault。
- 每次解析 Key 与最终入库重查服务端用户、会话有效期/撤销、账户 active、密码哈希指纹及精确 Key 引用。密码变化/账户失效后旧引用即不可用；完整密码找回/账户注销仍未实现，不能宣称已交付这些产品功能。
- 前端 password 输入发送即清空；无 localStorage/sessionStorage、URL、长期 Cookie、任务消息、日志或完整 Key 回显。只返回 configured/expiry，不返回前缀或尾号。SDK 所需明文仅短暂存在于受控调用对象；Python 内存加密不等于可证明零残留或抵抗进程完全被攻破。
- 重启销毁密钥，已保存任务/候选继续保留；孤立排队任务在执行或读取恢复时失败，不调用模型。需要再调用时重新配置、预览并授权。

使用成熟维护的 [cryptography Fernet](https://cryptography.io/en/latest/fernet/)，没有自制加密算法；新增二进制依赖约 3.6 MiB 下载与短暂内存运算，没有常驻外部服务成本。**不支持多 API worker/独立真实 Worker**。标准启动明确 `--workers 1`；WEB_CONCURRENCY/UVICORN_WORKERS 非 1 时拒绝本地真实配置。CLI worker 参数、跨主机 Broker、私有运行账户、HTTPS、内存转储策略需部署批次专门验证。

## 4. 授权、发送范围与费用

Key 配置、免费预览、收费创建为三次独立动作。预览绑定 owner/session/Key 引用、简历版本、发送 SHA-256、模型、授权版本和十分钟有效期；POST 必须携带 accepted=true 和 max_calls=1 的精确类型。过期、换 Key、跨用户/会话/简历的预览被拒绝。授权快照只在有界内存保存；运行只持久化必要 ID/版本/哈希。

仅发送预览中的教育、技能、项目、实习/工作文本及固定 Prompt，PDF 原件不发送。标题前的个人资料区、已识别无关区、联系信息/网址、标注姓名/地址等默认用等长遮罩，证据仍对原始 raw_text 和服务端坐标校验。JSON 序列化字节量加固定余量作为 token 保守上界（不是精确 tokenizer）；输入最多 50,000 字符，输出最多 4096 token。无法识别章节则拒绝预览，不退回全文发送。

规则脱敏不能识别所有混排、未标注姓名或嵌入经历的第三人信息；用户必须检查全文预览，可取消并提供更干净的 PDF。该残余风险需真实格式评测，上线前不能承诺全面匿名化。

真实运行 `max_attempts=max_calls=1`；每用户最多一个 queued/running，默认全局 active 上限 20，单 dispatcher 顺序执行。DB 写事务串行准入、幂等请求返回原任务、领取 CAS 和调用前计数保护重复投递。终态再次调用须新预览、新主动授权；同一授权不能另建第二任务。旧真实 run 的 retry 永远拒绝。

先持久化调用次数与 unknown，再发请求。401/402/400/422/429/5xx、超时、断网均不重试；没有 usage 时保守保留费用未知，不能假设没有扣费。usage 仅保存经过类型/范围/总数检查的输入、输出、总 token；不将其展示为确定账单。取消、删除、退出、换 Key 或版本变化令牌失效；迟到 usage 可更新最小计量，迟到事实不能入库。已发请求仍可能收费，界面明确告知。

## 5. API、迁移与个人数据

| API（/api/v1 前缀） | 用途 |
|---|---|
| GET /ai/deepseek-key | 当前会话 Key 配置、期限与功能开关 |
| PUT /ai/deepseek-key | SecretStr 临时 Key；不验证供应商、不收费 |
| DELETE /ai/deepseek-key | 撤销 Key、预览和相关 active 运行 |
| GET /resumes/{id}/extraction-preview | 本人脱敏预览、模型、预算、隐私与费用说明 |
| POST /resumes/{id}/extraction-runs | 原接口扩展 deepseek+显式授权；mock 兼容 |
| GET /resumes/{id}/extraction；GET /extraction-runs/{id} | 恢复持久状态/usage；不能触发模型 |

原候选/read/cancel/retry/review API 延续身份、CSRF、Origin、对象授权和 revision。没有客户端 owner 或公开候选注入入口；前端不访问数据库。OpenAPI 实际生成 TypeScript。

0006 追加 session_id/key_reference/authorization_id/model/consent_version/sent_text_sha256/billing_status/token 三列，扩展 CHECK；真实预算为一、身份与授权引用不可空。没有 Key 列或完整 Prompt/模型响应列。SQLite batch 更换运行表会触发候选 FK SET NULL，因此迁移保存并恢复原引用，外键持续开启，独立旧数据测试验证保留。真实运行历史存在时禁止有损降级。

新增引用用于授权围栏、版本用于复现、token 用于计量；不进普通日志。删除简历清候选、正文/定位和正式来源原文，保留用户已确认值及最小运行终态/计量。运行与候选目前随本机数据库保留；正式保留期限、完整账户导出/删除、备份删除及供应商侧删除流程仍属上线条件，未虚构自动按天清理。

## 6. 中文页面

“我的资料”内可选择已上传 PDF，独立配置/撤销会话 Key，预览外传文本、服务商/模型、token/费用/个人信息说明，并主动勾选后点击“开始 AI 解析”。未配置/关闭时按钮禁用；解析期间读取真实任务状态、最多自动查询 70 次，提供手动刷新和取消。失败说明下一步，unknown 要求先查供应商账单。

成功后明确展示 AI 待审核，用户点击载入候选；有画像草稿时防止载入覆盖，继续沿用四组逐项确认/编辑/拒绝。真实模型/运行来源延续到正式资料，用户手工值独立标记。刷新/重新登录恢复持久任务、候选和正式值；重新登录不恢复 Key。无综合准确率或自信分数。1440/768/390 授权弹窗截图仅合成资料，已检查布局并调整弹窗宽度与链接换行。

## 7. 免费验证结果

最终免费验证结果：

| 验证入口 | 实际结果 |
|---|---|
| backend/.venv/Scripts/python.exe -B -m unittest discover -s tests -q | **255/255**，125.300秒，失败0/跳过0；原222保留，新33 |
| 新BYOK/计量/评测/迁移专项 | **15 + 6 + 10 + 2**项，纳入全量；包含旧ref不误撤新Key、超usage丢弃、空/未知停止、一对一重复评测 |
| npm run test | **49/49**，13.18秒；原39与新增10 |
| npm run test:e2e | **18/18**完整Chromium，最终全量1.5分钟；相关BYOK两场也已复验通过 |
| npm run api:check、typecheck、lint、build | 通过；生成契约无漂移，生产build仅本机验证 |
| tests/http_file_lifecycle_smoke.py | 真实localhost HTTP通过；临时库与服务清理 |
| evaluate_deepseek_v2.py（无授权标志） | exit0，6个计划合成输入，paid_calls=0，质量待验收 |
| evaluate_resume_extraction_v2.py | 原11个固定合成案例免费重跑，0护栏失败，报告内容未变；不代表真实AI质量 |
| git diff --check、旧迁移差异与清理 | 通过；0001～0005无差异，仅新0006；变更源文件扫描未发现Key形态值或秘密文件，E2E端口3001/8001监听数为0 |

测试分为既有 Mock 核心、真实会话/SQLite 故障注入、官方 SDK 无网络模拟；Chromium 使用真实 FastAPI/Next/临时迁移库，并在私有 E2E launcher 替换付费 transport，**不能作为真实 AI 准确率或真实供应商网络验收**。

覆盖 Key 密文内存/过期/撤销/用户与会话隔离、密码/账户失效、无 Key/开发者环境 Key 不回退、显式授权、401/402/429/参数/网络/截断/空输出、严格 Schema、日志安全、调用前预算、双执行/双会话并发准入、取消/删除/退出/原文变化迟到、孤立队列恢复、候选不自动确认、用户审核来源、旧迁移/回归。原有 V1 内存测试保留，旧环境 Key 驱动收费 CLI 改为拒绝调用的验证。

## 8. 供应商资料与发布阻塞

查阅日 2026-10-12，全部为官方来源：

- [模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[Chat API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)：JSON/非思考/输出及 usage 参数，不视 JSON 格式为事实校验。
- [隐私政策](https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html)（2026-02-10）：覆盖 API，同时明确下游应用须自行披露规则；一般政策描述输入/输出可能经处理用于训练、产品设置退出和境内存储。**未确认该设置如何适用于 API、API 确切保留期限或零保留配置**。
- [硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)：存在落盘缓存；未使用后通常数小时至数天清理。这不是整体 API 数据删除承诺，本批没有经核实的关闭缓存接口。
- [开放平台协议](https://cdn.deepseek.com/policies/zh-CN/deepseek-open-platform-terms-of-service.html)（2026-04-29 生效）：2.4 限制 Key 共享/客户端暴露；开发者责任条款明确下游个人信息处理责任。**第三方产品服务端 BYOK 托管是否被允许需供应商确认**，不能由用户拥有 Key 推断获得代理使用许可。

发布阻塞：供应商 BYOK 使用许可、API 保留/训练退出/数据处理配置与请求适用范围；真实质量和实际 API 参数/时延验收；残余个人信息脱敏；单进程到正式部署的安全方案；既有账户导出/注销、HTTPS/ACL/备份、解析隔离及 PostgreSQL 并发/容量。生产真实开关当前无法开启，无公网部署。本批本地代码能力不等于已批准处理真实用户简历的生产服务。

## 9. 后续真实评测入口

`backend/evaluations/evaluate_deepseek_v2.py` 默认 dry-run，0 调用。付费需负责人提供 authorize 标志、调用数、输出上限、人民币预算、亲自核实的当前最高输入/输出单价、全新报告路径，交互输入“同意付费评测”，再由 getpass 输入自己的 Key；不读环境/命令行 Key。

最多六个纯合成输入，复用独立 gold，覆盖正常四组、毕业未知、多经历日期、相似技能、否定/计划、注入和正反冲突。调用前写最小账本，空候选、未知计量、任一失败或严重错误停止后续调用并返回非零；报告存在时拒绝重放。报告记录请求模型及别名核对日期、Prompt/Schema/脱敏版本、一对一匹配的逐字段 Precision/Recall/错误/遗漏、重复候选、证据定位、严重可确认错误、gold 不一致候选比例、token 和调用次数；不保存 Key、原简历/Prompt、原模型 JSON，不宣称 gold 差异等于真实人工修改成本。实际费用由负责人核对账单，字段保持未知。

本批仅运行 dry-run 和无网络门槛测试，未执行上述付费命令。负责人另行明确授权后，可先一条合成样本验证实际请求和 usage，再扩大到六条；后续获授权、去除个人信息、多样独立真实 PDF 需要单独标注与人工复核。零严重错误、证据归属、召回与修订成本的发布门槛应在实测前明确，不能用 Mock 通过数推断质量。

**停止点：完成本轮允许范围和免费验收后停止。真实调用待验收；不进入 S3。**
