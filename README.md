# OfferPilot

当前交付为 **S1 中文账户/私有 PDF + S2 可信画像与受控解析 + S3-1 私有职位管理与 JD 条件审核**。“职位”支持粘贴或手工导入、搜索筛选、编辑删除、收藏、免费规则整理及原文对照审核；未审核或旧 JD 条件不会变成可信匹配输入。没有匹配分数、岗位推荐或投递功能。运行与验证见 [S3-1 验收](docs/S3-1-acceptance.md)、[条件设计](docs/job-structure-design.md) 和 [PLAN](docs/PLAN.md)。

S3-1 不调用模型、不抓取招聘网站、不改变正式画像。本轮真实模型请求0次；S2历史已单独完成一次正常合成样本请求，字段19/19、证据21/21且候选全部待审核，不能代表总体质量，见 [单样本报告](docs/S2-2B-single-real-evaluation-2026-10-14.md)。S2其余真实评测没有继续，生产真实模式仍关闭，既有供应商与上线条件继续保留。此前批次的准备说明及“0调用”记录为历史。

2026-10-14 完成 S2-2B 正式模型验收准备收尾：最小修复 SDK 响应形状、原始 usage 类型与压缩边界，补全版本化 V2 Prompt；准备五份合成样本和免费协议/故障验证。兼容性结论、全部免费结果和剩余阻塞见 [准备报告](docs/S2-2B-readiness-review.md)、[供应商协议审查](docs/S2-2B-provider-policy-review.md)。本轮仍未执行真实请求或收费命令。

S2-2B-Fix 已将评测入口接到实际合成 PDF、正式上传/私有存储/解析、脱敏预览、持久运行和候选审核。免费报告保留公开合成输入及实际候选/原文/页码；Mock token 保持未知，所有结果明确标记测试。终端 Key 输入无 TTY 或无法验证隐藏输入时失败关闭；未来真实入口仅允许首份、一次请求且不自动审核。五个原阻塞与免费复验见 [修复验收](docs/S2-2B-first-real-preflight-fix.md)。


## 本地启动（Windows PowerShell，两个终端）

需要 Python 3.11、uv 和 Node.js 22.12+（本机使用 22.16）。按锁文件安装；S3-1没有新增依赖。现有 cryptography 50.0.2 用于 Fernet 内存密钥加密，无 Redis。后端 head 为 0007（私有职位），0001～0006 不变；前端继续使用生成 OpenAPI 契约。

**终端 A：后端。** 显式使用一个本机开发库。相对 SQLite 文件路径固定解析到 `backend/`；以下命令不会迁移默认旧库。新库升级到 0007；已有本机库应先备份再执行同一向前升级命令，可复用私有目录。不要切换为生产地址。本批只迁移隔离测试库，没有替你修改现有开发或生产库。

```powershell
Set-Location 'D:\agent 项目\OfferPilot'
uv sync --project backend --locked
$env:OFFERPILOT_ENVIRONMENT = 'development'
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///./data/s1-3-local.db'
$env:OFFERPILOT_UPLOAD_DIR = 'D:/agent 项目/OfferPilot/backend/uploads/s1-3-local'
$env:OFFERPILOT_TRUSTED_ORIGINS = '["http://localhost:3000","http://127.0.0.1:3000"]'
& .\backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
# 替换为本机测试邮箱；输出的邀请码仅向被邀请者提供，不放到日志或报告。
& .\backend\.venv\Scripts\python.exe -m offerpilot_api.identity.invites --email student@example.com --expires-hours 48
& .\backend\.venv\Scripts\python.exe -m uvicorn offerpilot_api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**终端 B：前端。** 环境变量仅服务端使用，不加 `NEXT_PUBLIC_`。可将同样内容保存在忽略的 `frontend/.env.local`；示例为 `frontend/.env.example`。

```powershell
Set-Location 'D:\agent 项目\OfferPilot\frontend'
npm.cmd ci
$env:OFFERPILOT_API_URL = 'http://127.0.0.1:8000'
$env:OFFERPILOT_WEB_ORIGINS = '["http://localhost:3000","http://127.0.0.1:3000"]'
$env:NEXT_TELEMETRY_DISABLED = '1'
npm.cmd run dev
```

打开 [本机中文工作台](http://127.0.0.1:3000)，点击“邀请注册”，使用签发时的同一邮箱、自己设定的密码及邀请码注册，然后登录。邀请码为 43 字符、邮箱绑定、默认 48 小时、仅能消费一次；注册不自动登录。没有公开签发邀请码、邮件验证、密码恢复或账户注销按钮。退出会撤销 FastAPI 服务端会话。

需要验证生产构建时，在终端 B 停止 dev 后执行 `npm.cmd run build`、`npm.cmd run start`；仍只绑定本机。这是本地构建验证，不代表生产上线配置。两个终端分别 Ctrl+C 停止服务。

若 npm 连接 `127.0.0.1` 代理失败，检查当前终端的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY；仅在确认不需要该失效代理后清空这三个进程环境变量。不要关闭 TLS 校验或修改全局代理来绕过问题。

## S3-1：中文职位管理

登录后进入“职位” → 添加职位 → 粘贴招聘 JD（或手工录入） → 填写名称及已知信息 → 显式点击“免费规则整理” → 对照原文逐条确认、修订、删错或补充 → 保存。只有岗位名称必填；未审核条件可保存后继续。所有数据只属于本人，刷新和重新登录从数据库恢复，不需要配置模型 Key。

列表支持岗位/公司/城市搜索、工作类型/收藏/审核状态筛选及排序；详情显示原文、条件、引文行号/字符区间、来源、创建/更新时间和版本。改 JD 后旧条件需重新绑定审核；并发409保留输入。来源链接仅安全保存/展示，后台不访问；当前没有匹配分数或自动申请。

| 方法与路径 | 行为 |
|---|---|
| POST /api/v1/jobs | 基本信息、JD、幂等键、可选条件；省略条件时免费规则生成pending |
| GET /api/v1/jobs | 本人分页列表；q/work_type/is_favorite/review_status/sort/page/page_size |
| POST /api/v1/jobs/parse-preview | 仅本地规则预览，所有条件pending，无模型/网络 |
| GET /api/v1/jobs/{id} | 本人详情、条件和历史JD快照 |
| PUT /api/v1/jobs/{id} | revision + 基本信息/JD/完整条件集合，允许部分审核进度 |
| DELETE /api/v1/jobs/{id} | JSON revision；清职位、快照及条件，保留最小防重回执 |
| PATCH /api/v1/jobs/{id}/favorite | revision + is_favorite |
| GET /api/v1/jobs/{id}/requirements | 本人条件及证据/审核/版本 |
| POST /api/v1/jobs/{id}/requirements | revision + requirement，新增一条 |
| PUT /api/v1/jobs/{id}/requirements | revision + requirements，全量保存审核进度 |
| PUT /api/v1/jobs/{id}/requirements/{rid} | revision + requirement，修改一条 |
| DELETE /api/v1/jobs/{id}/requirements/{rid} | JSON revision，删除一条 |
| POST /api/v1/jobs/{id}/requirements/{rid}/review | revision + action confirm/reopen |

review_status筛选值pending/reviewed；sort值updated_desc/created_desc/title_asc。所有写接口复用会话与CSRF，既有/未知跨用户资源统一404。每人1000个岗位，每JD30,000字符、每岗位100条条件/100份快照；规则可能漏分或保守归类，长于2000字符的句段保留全文并提示手工拆分，不伪装AI理解。无需修改开发或生产数据；仅在自行备份并选定的本机库上运行现有 Alembic head 命令。

## S2-2B：本机中文 BYOK 与授权解析

完成上述本机依赖/新库步骤后，**负责人自行决定**是否在终端 A 启动前设置：

```powershell
$env:OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED = 'true'
$env:OFFERPILOT_BYOK_TTL_SECONDS = '3600'
# 必须保持 development、localhost、--workers 1；默认开关为 false。
```

进入“我的资料”，配置自己的 Key → 选择简历 → 预览发送内容与费用 → 主动勾选 → 点击“开始 AI 解析”。配置与预览不收费，开始解析最多调用一次官方 deepseek-flash（非思考），输出上限4096 token，不自动修复或重试。界面显示外传个人信息、脱敏局限、输入保守上界及费用不确定性。失败/取消可能已经收费；应先查 DeepSeek 账单。成功只创建待审核候选，逐项确认/修改/拒绝后才形成正式画像。

Key 在后端受控密文内存中最长一小时，同时受登录期限限制；不写业务DB/浏览器存储/URL/任务消息/日志，不回显。退出、撤销、换Key、过期、密码/账户失效或服务重启后旧引用不可用。重新登录恢复任务、候选和已确认资料，但 Key 必须重新配置。当前方案不支持多进程共享，生产配置无法开启；不要设置开发者 API Key 作为回退。

新增 `GET/PUT/DELETE /api/v1/ai/deepseek-key`、`GET /api/v1/resumes/{id}/extraction-preview`；原 create/run/candidate/review API 扩展并复用。API Schema、0006字段用途/删除边界、供应商资料和发布阻塞见 [S2-2B 验收](docs/S2-2B-acceptance.md)。

**S2 历史免费评测预览入口（S3-1 不继续真实评测）：**

```powershell
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py
```

默认只规划第一份，0调用，不构造 Provider；免费运行全部五份固定响应可执行：

```powershell
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py --mode mock --case-id normal_chinese --demonstrate-review
```

后续负责人明确提供授权、次数上限和预算后，收费命令及分批流程见 [执行手册](docs/S2-2B-real-evaluation.md)。必须同时提供 `--mode real --authorize-paid-calls --authorization-record`、调用/token上限、人民币预算、执行当天核实的最高单价及日期、全新报告路径，并交互输入“同意付费评测”和 `getpass` Key；不读环境或命令行 Key。首次只选 `normal_chinese --max-calls 1`，负责人审核后再逐份另行授权。预算不足、陈旧价格、重复账本、任一失败/未知结果均停止，不补发。

五份合成白名单覆盖正常、否定/计划、多经历、时间歧义及注入；报告记录字段 Precision/Recall、证据定位/归属、错误候选、版本、token、预占与实际调用边界计数；实际费用须核对供应商账单。免费协议报告中的100%仅验证固定响应，不代表真实模型质量。旧 evaluate_resume_extraction.py 收费路径持续禁用。本人本机合成 CLI 评测不解除第三方服务器 BYOK 和真实个人数据处理的发布阻塞。

评测只在系统临时目录创建新 SQLite/合成账户/私有存储，经0001～0006实际迁移；不接触默认开发库。每个样本独立隔离，退出销毁库、Key与原件，仅保留显式输出的公开合成报告。可查看 [首份PDF](backend/evaluations/fixtures/normal_chinese.synthetic.pdf) 和 [完整Mock审核报告](backend/evaluations/s2-2b-pdf-mock-report.json)。`--demonstrate-review` 是免费Mock预设的显式确认/拒绝演示；真实模式不允许该标志，候选只保持pending。不要在聊天、参数或环境中提供真实 Key；通过现有Web BYOK或经验证的原生终端安全输入。

## 前端验证与真实浏览器验收

```powershell
Set-Location 'D:\agent 项目\OfferPilot\frontend'
npm.cmd run api:check
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test
npm.cmd run build
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

E2E 自行启动 8001 的真实 FastAPI 和 3001 的独立 Next 服务，迁移新临时 SQLite 库、签发合成测试邀请并生成合成 PDF；不使用开发/生产数据。端口占用会拒绝运行，不复用现存服务。可设置 `OFFERPILOT_E2E_API_PORT`、`OFFERPILOT_E2E_WEB_PORT` 为两个空闲端口；后端 Python 默认为仓库 venv，特殊位置可设 `OFFERPILOT_E2E_PYTHON`。不要同时用多个 E2E 进程写同一个 `.next` 目录。

身份、文件与画像 API 使用真实后端；AI场景仅在私有 launcher 使用无网络 Provider 故障注入，不调用 DeepSeek。故障恢复用测试进程内的受限控制通道制造实际文件系统异常、修改原件字节、撤销会话和停启服务；控制端点不属于正式 FastAPI app/OpenAPI，前端代理也不开放它们。密码、邀请、Cookie 和控制秘密不写日志、trace 或截图。正常退出关闭服务、数据库并删除临时测试根目录；清理失败使测试失败。截图只包含合成资料，保存在忽略的 `frontend/artifacts/screenshots/`，运行全套 E2E 可重生各批截图；S1原24张、S3-1新增24张，验收详见 [S1-3](docs/S1-3-acceptance.md) 与 [S3-1](docs/S3-1-acceptance.md)。

当后端 API 改动时，在仓库根目录执行 `backend\.venv\Scripts\python.exe frontend/scripts/export-openapi.py`，再在 frontend 执行 `npm.cmd run api:generate`、`npm.cmd run api:check`。导出仅读取 Schema，不迁移数据库或读取用户记录；生成类型提交，`.next`、node_modules、测试截图与运行数据不提交。

## 使用 API

本机 OpenAPI：`http://127.0.0.1:8000/docs`。注册不会自动登录。

| 方法与路径 | 行为 |
|---|---|
| `POST /api/v1/auth/register` | JSON：`email`、`password`（12～128 字符）、`invite_token` |
| `POST /api/v1/auth/login` | JSON：`email`、`password`；返回 `user`、`csrf_token` 并设置会话 Cookie |
| `GET /api/v1/me` | 当前用户的公开账户字段 |
| `GET /api/v1/auth/csrf` | 有效会话下获取 CSRF token，供页面刷新后恢复 |
| `POST /api/v1/auth/logout` | 撤销当前服务端会话，删除 Cookie；需 CSRF |
| `GET /api/v1/profile` | 当前用户正式四组画像、独立候选和完成提示；空画像 revision 0，不制造经历 |
| `PUT /api/v1/profile` | JSON revision + education/skills/projects/experiences 四数组全量保存；允许部分字段为 null，id 可省略新建，缺席原条目被删除；需 CSRF |
| `POST /api/v1/profile/candidates/{candidate_id}/review` | JSON revision + action confirm/modify/reject；modify 必须 value，其他 action 不携 value；支持性不足不可原值确认；需 CSRF |
| `POST /api/v1/resumes` | multipart `file` 上传；需 CSRF；保留 `/resume/upload` 兼容入口 |
| `GET /api/v1/resumes` | 仅本人简历元数据列表 |
| `GET /api/v1/resumes/{resume_id}` | 本人简历元数据 |
| `GET /api/v1/resumes/{resume_id}/download` | 本人原始 PDF；鉴权、完整性验证、安全附件名、禁止缓存 |
| `DELETE /api/v1/resumes/{resume_id}` | 本人原件、正文、历史派生、候选及原文证据删除；保留用户已确认正式值并标记来源已删；需 CSRF；仅全部物理清理完成返回 204 |
| `GET /api/v1/resumes/{resume_id}/lifecycle` | 本人处理/删除状态、安全错误码及尝试次数；含失败及最小删除完成记录 |
| `PATCH /api/v1/resumes/{resume_id}` | JSON `file_name` 修改显示名；不移动文件；需 CSRF |
| `GET /api/v1/resumes/{resume_id}/profile` | 读取已有画像；不发起 AI 或确认事实 |
| `GET /api/v1/resumes/{resume_id}/profile/facts` | 读取已有事实；不生成或修改候选 |

所有受保护接口从 HttpOnly Cookie `offerpilot_session` 解析身份。客户端需保存并随请求携带 Cookie；写请求额外发送登录响应中的 `X-CSRF-Token`，不发送可信 `user_id`。Swagger UI 可用于查看结构和发起登录；写接口使用支持自定义请求头的 API 客户端操作，当前 Swagger UI 未增加 CSRF 请求头管理。

登录/注册只接受 JSON，并检查实际提供的 `Origin` 与 `Sec-Fetch-Site`。默认可信来源为本机 8000；无 Origin 的 CLI 请求允许使用，会话写操作仍需 CSRF。Cookie 为 HttpOnly、SameSite=Lax、固定生命周期（默认 12 小时）；非 development 强制 Secure，需 HTTPS。无自动延长；重新登录轮换当前浏览器旧会话；退出立即在数据库撤销。错误只返回安全 code/message/字段名，旧上传与资源错误保留 `detail` 字符串兼容。

新上传的 owner 仅取服务端身份。跨用户、不存在、历史 NULL-owner 资源统一 404，无认领/转移接口。旧按简历画像/事实经父链授权，属于历史派生数据，不能作为正式画像信任；新 career_profiles 按当前用户唯一，entry/candidate ID 每次校验归属。客户端 user_id/来源字段不被接受；响应不含本地路径或简历全文。邮箱、画像值和证据不写日志。

正式画像日期使用 YYYY-MM、未知为 null，毕业时间区分预计/实际，经历状态区分进行中/已结束/未知，不推算年限。手工保存标用户填写，原值确认标简历候选经用户确认，修订标用户提供内容；模型自报置信度不代表正确率。所有写操作检查 revision，旧版本 409 不覆盖数据。资料页面保留冲突输入，支持查看最新保存内容；主动重新载入会明确提醒丢弃本次编辑。

候选由内部 ingest_profile_candidates 经版本化 Schema、owner/文件状态/证据及批次幂等校验入库。每批最多 100、每用户候选上限 500；相同批次同内容复投不加版本，不同内容 409。S2-2A 的 Mock 任务与候选一次提交，候选始终 pending；真实模式通过下文独立 BYOK/授权入口运行。

## S2-2A 隔离 Mock 验证

推荐直接执行现有 `npm.cmd run test:e2e`：launcher 创建临时 SQLite，经 head 迁移、生成登记的合成中文 PDF，使用真实本机 API 和页面完成任务与审核。默认运行环境保持 Mock 关闭；未登记简历会安全失败，不能用于真实 AI 抽取。

如需独立调试，只在自行选定的隔离 development 库/私有目录中开启后端 `OFFERPILOT_MOCK_EXTRACTION_ENABLED=true`，前端服务另开 `OFFERPILOT_MOCK_EXTRACTION_UI=true`。两项均为服务器配置，不包含 Key。默认租约30秒、最多两次执行/共四次调用；创建传入当前 source_version 和幂等键，重复任务不会重复入库。运行 API 见生成 OpenAPI：

| 方法与路径 | 行为 |
|---|---|
| GET /api/v1/resumes/{id}/extraction | 开关、资源版本、最近运行 |
| POST /api/v1/resumes/{id}/extraction-runs | `{idempotency_key, source_version, mode:"mock"}`，202 queued |
| GET /api/v1/extraction-runs/{id} | 持久状态/版本/次数/安全错误 |
| GET /api/v1/extraction-runs/{id}/candidates | 仅该运行、同用户候选 |
| POST /api/v1/extraction-runs/{id}/cancel 或 /retry | `{revision}`；CSRF、状态、资源版本与剩余额度 |

Mock 模式 lifespan 执行器读取持久 queued；关闭服务不丢排队记录，过期 running 可查询失败。相同配置下也可执行有限处理/恢复（不要指向真实开发或生产数据）：

```powershell
& .\backend\.venv\Scripts\python.exe -B -m offerpilot_api.agent_runtime.process_extractions --limit 10
& .\backend\.venv\Scripts\python.exe -B -m offerpilot_api.agent_runtime.process_extractions --recover-only --limit 10
# 独立字段评测只用固定合成响应，不联网，不接收 API Key。
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_resume_extraction_v2.py --output backend/evaluations/s2-2a-synthetic-report.json
```

评测把独立 gold 与候选逐字段比较并报告错/漏/多字段、保守护栏和位置覆盖。包含刻意错误的模型输出，不能据此计算真实 AI 质量；详见 [合成评测报告](backend/evaluations/s2-2a-synthetic-report.json)。旧真实 DeepSeek 评测脚本仍属另行授权入口，本批不执行。

## 私有文件与故障恢复

`LocalResumeStorage` 提供 `save/read/delete`。新对象使用服务端随机 UUID 文件键，数据库记录字节数、SHA-256 和独立处理/生命周期状态；显示名不会成为路径。存储根目录不得由 Web 服务器公开，Windows 上需保证目录 ACL 仅可信服务账号/操作员可用（POSIX mode 不能代替 Windows ACL）。不接收客户端对象键、路径或归属。

每次上传先提交 `uploading` 文档和清理记录，再写入对象。`.claim` 保存已入库的简历 ID 作为创建证明，`.part` 为未完成文件；均在同一私有目录，禁止对外暴露或写日志。证明先写入并 fsync，完整 PDF 以不覆盖已有对象的方式发布，内容/页数/文本限制与旧接口相同。解析完成才进入 `parsed`，并清除上传记录；失败补偿会删除实际 PDF/part/claim 和文档。进程中断的记录可由恢复命令追踪；对象键碰撞、缺失/损坏证明会安全保留失败记录，绝不删除无法证明归属的文件。

删除先提交 deleting、清正文及旧 profiles/profile_facts，并清除新候选与原文证据、推进相关画像 revision；普通文件接口立即隐藏。保留用户已确认的正式值并标来源已删除，独立手工资料保留。再删 PDF/part/claim，最后硬删文档。物理删除或最后数据库提交失败均返回 **503 + 安全 code/message + Location**，不伪装 204；可查询 lifecycle 并重试同一 DELETE。同 owner 已完成 DELETE 重复为 204，其他用户/NULL-owner/未知仍 404。完成记录只留 owner、简历 ID、状态、时间和次数，清所有对象引用；保留期限与账户删除政策尚待决定。

已缓冲的下载可完成，删除意图提交后的新下载被拒绝；文件句柄在生成响应前已关闭，避免 Windows 锁妨碍删除。本机数据库写锁覆盖受限保存/解析/下载/删除，迟到完成须 CAS 校验 active 状态；当前无异步 Worker。多用户规模与解析超时隔离须在上线批次验证。

**显式运维恢复（development，本轮没有后台守护进程）：** 先确认环境变量指向需要恢复的本机库与私有目录，再执行：

```powershell
& .\backend\.venv\Scripts\python.exe -B -m offerpilot_api.resume_ingestion.recover --stale-upload-seconds 3600 --limit 100
```

每次只尝试至多 100 个已删除失败/待完成删除或超过一小时的未完成上传（可设置 1～1000 个，上传阈值至少 60 秒）；活跃上传受数据库锁和状态检查保护。只输出 attempted/completed/failed/skipped 计数，有失败退出 1；之后可人工重跑，不自动无限重试。通常 DELETE 重试即可恢复文件锁/临时 IO 故障或“文件已删、最后提交失败”。若 `.claim` 在写入时被中断为空/半写，或引用冲突/越界，无法证明归属，必须停止相关写入，由可信操作员核对数据库清理记录与存储后处置；命令不会扫描或删除未知文件，也没有绕过证明的 API。NULL-owner 历史数据完全不触碰；已归属的历史文件仅允许根目录内直接 UUID.pdf 且无 reparse/证明冲突的受限兼容路径。

本人原件缺失下载返回 410 `resume_file_missing`，存储不可用返回 503，哈希/大小不符返回 409；不包含路径或对象键。数据库逻辑删除及文件 unlink 不代表 SQLite 空闲页、备份或磁盘快照的安全擦除；备份保留、恢复后删除重放和完整账户导出/注销仍属后续范围。

## 隔离回归

```powershell
Set-Location 'D:\agent 项目\OfferPilot'
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///:memory:'
$env:OFFERPILOT_ENVIRONMENT = 'development'
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
& .\backend\.venv\Scripts\python.exe -B tests/http_file_lifecycle_smoke.py
git diff --check
```

测试创建临时 SQLite 与合成 PDF，身份运行真实 Argon2id、会话和授权；旧抽取回归使用 Fake Provider，不调用付费 API。生命周期核对原始字节/关联表/实际文件，画像验证授权、Schema、revision并发与证据删除。HTTP脚本迁移新临时库、启动随机本机端口，退出后关闭连接并清临时目录。S2-1 最终 **167/167后端、30/30前端单元、12/12 Chromium E2E** 通过，原125后端与S1页面不回归；API契约/类型/lint/build通过，证据见 [S2-1验收](docs/S2-1-acceptance.md)。S1历史结果保留原记录。

S2-2A 当批结果：**222/222 后端、39/39 前端、16/16 完整 Chromium**，真实 HTTP smoke、契约/类型/lint/build、迁移与差异检查通过。合成 PDF 全链中7条候选入库后正式画像仍为空，用户主动确认后才写正式值。并发/取消/删除/旧版本迟到、事务故障、丢提交确认与实际子进程中断/CLI恢复均覆盖；字段错误及定位局限另见 [本批验收](docs/S2-2A-acceptance.md)，不代表真实模型质量。

新迁移 `0003_resume_file_lifecycle` 在 0002 后增加文件元信息、处理/删除状态和独立清理记录；原生 SQLite 加列保留旧 owner、正文及父子关系，旧行默认 active/parsed，不回填对象键或认领 NULL-owner。只在无新元数据/状态/清理记录时允许安全降级；有数据须向前修复或备份恢复。S1-2 未修改 0001、0002；S1-3 未新增迁移，仅升级隔离测试库。PostgreSQL 运行行为尚未验证。

S2-1 追加0004新建三张独立画像/候选表，0001～0003不变；空库和含旧0003数据快照向前验证通过，原owner/NULL-owner/文本/文件元信息/旧事实保持、不自动确认。新数据存在拒绝有损downgrade，空0004可降回0003；不执行offline破坏性降级。PostgreSQL仍待独立验证。

## 当前边界

当前没有公开生产 BYOK 服务、总体模型质量结论、岗位匹配、密码找回/修改、邮箱验证、账户注销、数据导出或生产部署。真实任务只支持一个本机 API 进程；独立 CLI 不处理真实任务。供应商条款、脱敏长尾、HTTPS/ACL、账户生命周期/备份、PostgreSQL/容量/解析隔离仍须上线前验证。S3-1 完成后停止，不自动进入 S3-2。


S2-2B免费验收：后端255/255，前端49/49，完整Chromium18/18，HTTP、契约/类型/lint/build及旧迁移兼容通过。真实DeepSeek请求0次、费用0元，真实调用/质量待验收。细项与发布阻塞见 [S2-2B验收](docs/S2-2B-acceptance.md)。
