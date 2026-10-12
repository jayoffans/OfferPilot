# OfferPilot

当前交付为 **S1 中文账户/私有 PDF 工作台 + S2-1 可信求职画像基础**。登录后进入“我的资料”，分组编辑教育、技能、项目、实习/工作，保存部分资料，查看字段来源，审核已有候选；刷新及重登录恢复已保存数据。上传支持本机 development 文本型 PDF，仍仅保存/解析文件，不生成 AI 候选。BYOK/DeepSeek 真实抽取、岗位匹配、投递管理、完整账户导出/注销和公网部署仍未实现。分批证据见 [docs/PLAN.md](docs/PLAN.md) 与 [S2-1 验收](docs/S2-1-acceptance.md)，前端设计见 [frontend/DESIGN.md](frontend/DESIGN.md)。

## 本地启动（Windows PowerShell，两个终端）

需要 Python 3.11、uv 和 Node.js 22.12+（本机使用 22.16）。后端与前端分别按锁文件安装；S2-1 无新增依赖。前端保持 Next.js、TypeScript、Tailwind、Radix、Lucide 和生成 OpenAPI 契约，后端追加 0004 的三张画像/候选表。

**终端 A：后端。** 显式使用一个本机开发库。相对 SQLite 文件路径固定解析到 `backend/`；以下命令不会迁移默认旧库。新库升级到 0004；已有 S1-3 本机库应先备份再执行同一向前升级命令，可继续复用私有目录。不要切换为生产地址。本批只迁移隔离测试库，没有替你修改现有开发或生产库。

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
& .\backend\.venv\Scripts\python.exe -m uvicorn offerpilot_api.main:app --host 127.0.0.1 --port 8000
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

主闭环不使用 Mock API。故障恢复用测试进程内的受限控制通道制造实际文件系统异常、修改原件字节、撤销会话和停启服务；控制端点不属于正式 FastAPI app/OpenAPI，前端代理也不开放它们。密码、邀请、Cookie 和控制秘密不写日志、trace 或截图。正常退出关闭服务、数据库并删除临时测试根目录；清理失败使测试失败。截图只包含合成资料，保存在忽略的 `frontend/artifacts/screenshots/`，运行全套 E2E 可重新生成全部 24 张；验收详见 [docs/S1-3-acceptance.md](docs/S1-3-acceptance.md)。

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

候选只由内部 ingest_profile_candidates 经版本化 Schema、owner/文件状态/证据及批次幂等校验入库，正式 API 没有候选创建或 AI 触发入口。每批最多 100、每用户候选上限 500；相同批次同内容复投不加版本，不同内容 409。S2-1 本机验收使用合成候选夹具，不在产品页面注入假资料。后续 S2-2 需提供真实抽取到该 Schema 的转换及独立调用授权。

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

新迁移 `0003_resume_file_lifecycle` 在 0002 后增加文件元信息、处理/删除状态和独立清理记录；原生 SQLite 加列保留旧 owner、正文及父子关系，旧行默认 active/parsed，不回填对象键或认领 NULL-owner。只在无新元数据/状态/清理记录时允许安全降级；有数据须向前修复或备份恢复。S1-2 未修改 0001、0002；S1-3 未新增迁移，仅升级隔离测试库。PostgreSQL 运行行为尚未验证。

S2-1 追加0004新建三张独立画像/候选表，0001～0003不变；空库和含旧0003数据快照向前验证通过，原owner/NULL-owner/文本/文件元信息/旧事实保持、不自动确认。新数据存在拒绝有损downgrade，空0004可降回0003；不执行offline破坏性降级。PostgreSQL仍待独立验证。

## 当前边界

当前提供中文账户、简历文件和可信画像管理，未实现 BYOK/真实 AI 抽取、岗位匹配、密码找回/修改、邮箱验证、账户注销、数据导出或生产部署。过期会话和邀请记录保留，限流桶在后续认证请求时清理一天前数据，散列不等于匿名化。恢复须人工显式执行，没有 SLA/队列/云存储保证。验证服务只绑定本机、使用合成数据；**完成 S2-1 后停止，S2-2/S2-3 另行授权**。PostgreSQL、HTTPS、配额、解析隔离/超时、容量、备份和安全日志审计仍需后续验证。
