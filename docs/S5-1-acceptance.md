# S5-1 PostgreSQL 本地集成验收

开发日期：2026-10-16，最终收口：2026-10-17（Asia/Shanghai）。范围仅为现有 MVP 的数据库适配、事务与本地质量验证，不增加产品功能，不公网部署，不执行 S5-2。

**验收结论：S5-1 PostgreSQL 本地集成验收完成。** 真实 PostgreSQL 原 API/领域 **255/255**、专项 **47/47**、真实 HTTP **13/13**、Chromium **33/33** 全部通过，均无失败或跳过；SQLite 561 项及前端 125 项通过。迁移、合成历史升级、权限、事务并发、查询记录及独立恢复完成。这不代表生产就绪；本轮到此停止，不进入 S5-2。

## 1. 基线与 Git

开始读取 PLAN、PRD、架构、数据库、路线图、S4-2 验收、README，检查模型/Session、0001～0009、身份、文件、任务、画像、职位、匹配、申请和测试环境。没有既有 CI 工作流，不把本机执行描述为远程 CI 成绩。

原分支 `codex/s4-2-workbench` 有 39 个有效已修改/新增文件；先完整保存为本地检查点 **`0526b7b`**，再建立 **`codex/s5-1-postgresql`**。没有 reset、覆盖用户修改、远程推送或接触实际开发/生产数据库。此前报告的 554/125/33 是 S4-2 历史数字，本轮重新执行，不能直接沿用。

## 2. 版本与配置方案

| 项目 | 本轮决定与实际配置 |
|---|---|
| PostgreSQL | **18.6 (Debian 18.6-1.pgdg12+2)**，官方稳定版本；18 主版本支持至 2030-11-14，依据 [官方支持政策](https://www.postgresql.org/support/versioning/) |
| 镜像 | `postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c`，实际拉取并运行；无 latest 标签 |
| 驱动 | **`psycopg[binary]==3.3.3`**，pyproject 和 uv.lock 固定；成熟 psycopg 3 的 SQLAlchemy 官方方言，binary 方便 Windows 重现，不引入新 ORM/Agent 框架；维护依据 [psycopg](https://www.psycopg.org/psycopg3/)、[SQLAlchemy 方言](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg) |
| 唯一入口 | Settings 的 `OFFERPILOT_DATABASE_URL`，应用和 Alembic 复用 build_engine；普通 postgresql URL 显式归一到 psycopg 3 |
| SQLite | 仍允许 development 轻量开发；非 development 缺失 PostgreSQL/使用 SQLite/不完整连接配置立即失败，不回退 |
| 运行角色 | `s5_app`：业务 DML，无 SUPERUSER/CREATEDB/CREATEROLE/DDL、无 alembic_version 写权限；启动只读检查有效权限、继承角色及对象所有者 |
| 迁移角色 | `s5_migrator`：非超级用户，仅隔离测试需要 CREATEDB 和本库 DDL；不用于 HTTP 业务 |
| 池与超时 | pool_size=5、max_overflow=5、pool wait=5s、connect=5s、statement=15s、lock=5s、idle-in-transaction=30s、recycle=1800s、pre_ping |
| UTC | 每个 PostgreSQL 连接会话 timezone=UTC；日期行动继续保留 Asia/Shanghai 民用日期，不补时刻 |
| 秘密与日志 | 随机密码在忽略的 `.env.s5-postgres`，不进入 Git/参数/报告；SQLAlchemy echo=false/hide_parameters；安全 503 不含 SQL、连接串或参数 |
| Docker | 仅一个 PostgreSQL 服务、持久卷、健康检查、启动等待；`127.0.0.1:55432`，不监听公网，无生产集群 |

生产应用启动核查数据库实际权限，而非仅判断账号是否叫 postgres。SQLite 与生产启动路径分别验证；本轮本机 development 允许合成 PDF 上传/Mock，生产上传和真实模型的既有限制继续保留。没有把数据库适配当作生产发布开关。

PostgreSQL 普通 SQL 错误的主消息也可能带非法输入，因此仅关闭 statement/parameter 日志不足。复审用合成 CAST 复现后，Compose 追加 `log_min_messages=fatal`，并保留 `log_statement=none`、`log_min_error_statement=panic`、两项 parameter length=0、terse。独立日志探针核对真实容器日志；FATAL/故障观测与正式日志保留策略仍需 S5-3 评审。

## 3. 迁移和兼容性

**0001～0009 历史文件完全保留，head 仍为 `0009_application_tracker`。** 没有重建业务模型，也没有为了适配而回填、认领或确认历史事实。实测未发现需要新 DDL 的兼容问题；查询计划未形成需新增索引的证据，因此不增加空迁移。

真实新库升级到 head，并比较 Alembic 结果与 Base.metadata：表/列/类型/可空性、外键、唯一约束、CHECK、索引一致。实测 UUID 原生类型、UTC timestamptz、嵌套中文 JSON、唯一/非空/外键拒绝、关联级联清理和应用角色权限。JSON 保留既有类型，未无故改成 JSONB。

历史升级测试从 0001 开始，每次只升一个 revision，注入合成简历/旧画像事实、owner、正式画像/候选、0005 任务和候选 FK、0007 岗位/JD/条件、0008 匹配 JSON，逐列比对升级前已存在值。旧候选仍 pending，未产生正式条目，0006 后候选 run 引用保持。空库和历史测试都使用随机新库，禁止在默认开发库或生产库试验。

## 4. 事务与并发修复

| 范围 | 真实验证与必要修复 |
|---|---|
| 认证 | 多连接竞争消费一次邀请、邮箱唯一与邀请回滚、限流新桶/过期桶精确次数、退出撤销、CSRF、双会话隔离；密码恢复尚无实现，本轮不新增 |
| 文件 | 实际 PDF/claim、数据库约束提交失败后的补偿、重复/并发删除、物理清理失败与恢复、上传/删除串行、迟到写入拒绝、已确认来源清理和双用户隔离 |
| 任务 | 单任务多连接领取、调用计数账本、租约失效与重领、取消迟到、幂等冲突、候选/完成同事务、真实 flush/提交故障及进程中断恢复 |
| 准入 | 不同简历的行锁不能保护共享总量。新增固定服务端键 `pg_advisory_xact_lock`，仅围绕有界准入计数/写入，提交释放；不跨模型调用持锁 |
| 幂等 | 不同简历并发复用同 owner 请求键，唯一约束输家返回明确 409，避免误报存储故障 |
| 首次画像 | 不同来源任务同时完成并创建唯一画像时，用 savepoint 处理 UNIQUE 竞争，保留外层简历/任务事务；内部无指定 revision 可重新领取已提交行，用户显式旧 revision 仍 409 |
| 职位 | 新建时锁当前活跃 User，将每用户配额 count+insert 串行；JD 审核/revision 围栏、匹配同依据去重、动态失效/规则版本/双用户隔离 |
| 申请 | 同岗位重复创建、请求回执、同 revision 唯一获胜、状态/行动/分类事件原子性、完成/取消/终态一致、删除职位竞争及 RESTRICT、工作台统计一致 |

这些测试使用独立 SQLAlchemy Session/数据库连接，不借 SQLite 全局写锁模拟 PostgreSQL。原安全错误、对象授权、用户声明/确认和人工申请边界保持；没有实现账户注销、Browser Agent 或自动投递。

## 5. 最终测试结果

| 验证 | 通过 / 失败 / 跳过 / 未执行 |
|---|---|
| PostgreSQL 原有 API/领域集成（13 模块） | **255 / 0 / 0 / 0**，480.906s，exit=0；与专项合计 302 项真实 PG 验证 |
| PostgreSQL 专项（迁移、并发、池/超时/安全/服务端日志） | **47 / 0 / 0 / 0**，66.158s，exit=0 |
| 完整 SQLite 后端 | **561 通过、0 失败、45 项 PG opt-in 跳过**；实际命令发现 606 项，516.201s，exit=0；新加的另外 2 项 PG 用例另在上行实际执行 |
| PostgreSQL 真实 HTTP MVP | **13 / 0 / 0 / 0**，真实 localhost uvicorn + HTTP，app 受限角色 |
| 前端 Vitest | **125 / 0 / 0 / 0**，12 文件 |
| PostgreSQL 完整 Chromium | **33 / 0 / 0 / 0**，5.3 分钟，无重试，exit=0 |
| OpenAPI / ESLint | 最终均通过，exit=0，契约未变、lint 无错误/告警 |
| Production Build / TypeScript | 最终均通过，exit=0；在 Chromium 完成后顺序执行 |
| 真实 HTTP 文件生命周期 | 已通过，独立临时 SQLite；不替代 PostgreSQL 文件验收 |
| 迁移历史 / diff / 锁文件 | 0001～0009 无改动；git diff --check、uv lock --check --offline 均 exit=0 |

API 回归复用原测试方法的业务断言，只把测试 harness 的数据库/Settings 换成各自新建的真实 PostgreSQL；认证故障注入在 PG 用实际撤销 SELECT，避免依赖 SQLite DROP TABLE，不减少 HTTP/脱敏断言。纯规则/供应商协议测试继续由完整后端回归覆盖，真实外部模型请求 **0**。SQLite 的 561 项为此前 554 项加 7 项配置测试，PG opt-in 跳过不代表 PG 验收跳过；改动后的认证故障 SQLite 原分支另聚焦 1/1 通过。后端原来没有独立 formatter/typechecker，不虚报执行。

真实 HTTP 旅程：邀请注册并独立登录 → 上传合成 PDF/字节一致下载 → 明确保存正式画像 → 粘贴合成 JD → 免费规则整理并审核 → 确定性匹配 → 对照双方证据 → 创建申请 → 已投递但未虚构投递时刻 → 只设日期行动 → 工作台及双用户隔离 → 完成行动核对统计 → 退出/重新登录恢复。候选池不自动成为事实，全程不调用 DeepSeek。

失败记录保留：首轮 Chromium 32/33，丢响应场景误将 Next.js 的全局路由 alert 当业务错误提示，导致过早查库；改为等待 main 中的实际“保存结果可能尚未确认”，保留原数据库与事件断言。早期恢复的 CHECK 反射存在等价 ARRAY cast 表达式差异，仅规范化该确切表达式，未忽略任何约束。前一天中断的未结束日志不计通过；测试库清理的异步 backend 终止竞态亦按实际修复记录，未给应用/迁移角色增权。

## 6. 查询与容量探测

机器为 Windows build 26200、Python 3.11.5，AMD Ryzen 7 7840H（8 物理/16 逻辑核）、内存 16,309,932,032 bytes；Docker Linux 分配 16 CPU / 7,897,784,320 bytes。最终记录时同机还执行隔离回归，存在资源竞争，不是独占负载或生产硬件。

合成规模：**100 模拟用户、300 简历、3,000 岗位及条件、1,000 匹配、2,000 申请、20,000 事件**；另有 HTTP 主旅程的 2 个账户、1 份 PDF/岗位/匹配/申请、3 条 JD 条件和 4 个事件。每个普通端点采样 20 次，4 并发工作台共 40 次；P95 按排序索引 `min(n-1, int(n*0.95))` 取值，20 次时恰为最大样本。215 次 HTTP 无 5xx、双用户/四用户隔离通过。

| 查询 | HTTP 中位数 ms | P95 样本 ms | SQL 次数（含认证） |
|---|---:|---:|---:|
| 工作台 | 70.947 | 190.160 | 12 |
| 申请 5 行 | 32.007 | 38.926 | 7 |
| 申请 20 行 | 39.239 | 46.210 | 7 |
| 岗位 5 行 | 26.728 | 29.444 | 5 |
| 岗位 30 行 | 29.031 | 32.982 | 5 |
| 申请筛选 | 36.879 | 46.723 | 7 |
| 时间线 | 27.911 | 35.279 | 5 |
| 工作台 4 并发、40 次 | 135.444 | 171.737 | 每请求有界 |

实测职位列表原来 5/30 行分别 9/34 SQL，确认为 N+1，改成当前页一次 group_by 后均 5；申请列表和工作台无随行数线性增长。最终 215 请求单条 SQL 最大 **27.424ms**（含驱动往返），超过 100ms 为 **0**；结束后超过 5s 的其他事务为 **0**。数据库约 **22,853,311 bytes**。

对恢复的独立合成库运行实际领域查询 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，包含工作台、申请、岗位与时间线；owner/FK 索引参与，小表顺扫未形成测得瓶颈。具体计划、硬件和空闲事务观测见 `quality-final/query-observations.json`，最终应用代码相同，无需无依据加索引。没有引入 Redis/缓存/额外队列或复杂 ORM。上述 P95 仅 20/40 个本机样本，不能推断正式容量、SLA 或长期慢查询分布。

## 7. 备份与隔离恢复

最终真实执行：**pg_dump 1,402,051 bytes / 0.820s，pg_restore 1.039s**。HTTP 写入全部停止后 dump 与私有文件快照共享一个静止一致点，恢复到另一个全新随机数据库和新的私有文件目录，不覆盖任何已有数据库。

校验 **22 表**全部行数及排序规范化 JSON SHA-256、列/PK/FK/唯一/CHECK/索引、revision **0009**；**301 份 PDF** 的大小/哈希/claim 归属逐一一致。`--no-owner --no-acl` 恢复后重新应用审核的权限，并实际验证业务 DML 可用、public CREATE 和 Alembic revision 写入被拒绝。

自动复现命令（包含新库创建与删除保护）：

```powershell
. ./scripts/postgres/env.ps1
& ./backend/.venv/Scripts/python.exe -B scripts/postgres/quality.py --users 100 --backup --output frontend/artifacts/s5-1/new-restore-run
```

脚本实际工具参数如下。PGPASSWORD/PGCONNECT_TIMEOUT 由受控子进程环境注入，源/目标库名来自刚创建的随机隔离库；备份字节用 Python 二进制文件句柄传输，不通过 PowerShell 文本管道：

```text
docker exec -i -e PGPASSWORD -e PGCONNECT_TIMEOUT offerpilot-s5-postgres pg_dump
  --host 127.0.0.1 --port 5432 --username s5_migrator --dbname <新建的源库>
  --no-owner --no-acl --format=custom
docker exec -i -e PGPASSWORD -e PGCONNECT_TIMEOUT offerpilot-s5-postgres pg_restore
  --host 127.0.0.1 --port 5432 --username s5_migrator --dbname <另一个全新目标库>
  --no-owner --no-acl --exit-on-error --single-transaction
```

dump 最低需要 CONNECT、schema USAGE、源表 SELECT；restore 需要目标库 owner/CREATE 和重建权限所需身份。本轮管理身份非超级用户。静止快照成功不证明并发文件备份正确：真实部署仍需协调上传/删除、文件版本、删除重放、备份加密/保留、异地/PITR 与 RPO/RTO，全部留待 S5-3。

## 8. 文件清单与复现

| 文件 | 目的 |
|---|---|
| `backend/pyproject.toml`、`backend/uv.lock` | 固定 psycopg 3 和二进制依赖 |
| `backend/.env.example`、`core/config.py`、`db/session.py`、`main.py`（后端 src/offerpilot_api 下） | 统一配置、受限池/超时/UTC、生产拒绝回退和角色权限核验、安全故障 |
| `agent_runtime/extraction_runs.py`、`profile/services.py`、`job_catalog/services.py` | 事务准入、幂等冲突、首次画像竞争、职位配额及测得的 N+1 |
| `.gitattributes`、`compose.postgres.yml`、`scripts/postgres/init.sh`、`start.ps1`、`env.ps1` | Windows clone 的 shell LF、固定最小 PostgreSQL、随机私有凭据、健康启动、角色与私有网络 |
| `scripts/postgres/regression.py`、`quality.py`、`backup_restore.py`、`observe_queries.py`、`verify.ps1` | 原 API 真 PG 回归、HTTP/规模、恢复/权限、实际查询计划和可重复检查入口 |
| `tests/__init__.py`、`tests/postgres/__init__.py`、`support.py` | 每项随机新库、Alembic、受限应用角色和安全清理 |
| `tests/postgres/test_migrations.py`、`test_auth_runtime.py`、`test_domain_concurrency.py`、`test_database_reliability.py`、`test_fixture_cleanup.py`、`test_server_logging.py` | 迁移、事务/并发、故障、角色、池、双角色连接清理与服务端日志的实际验证 |
| `tests/test_database_config.py`、`tests/test_authentication.py`、`tests/test_resume_upload.py` | 配置安全、PostgreSQL 实际权限故障注入及既有非开发上传拒绝断言适配 |
| `frontend/scripts/e2e-backend.py`、`frontend/e2e/applications.spec.ts` | 可选全新 PG Chromium 后端、准确等待业务错误而非路由播报 |
| `README.md`、`docs/PLAN.md`、`architecture.md`、`database.md`、`roadmap.md`、本文件 | 当前工程决策、复现命令、历史边界与验收 |

证据在忽略目录 `frontend/artifacts/s5-1/`，不提交数据库备份、私有合成对象、凭据或日志。最终质量 JSON 在 `quality-20261017/quality.json`、恢复报告在其 `backup/restore-report.json`；运行 `scripts/postgres/verify.ps1` 可重新生成一套新目录，依次检查 PG、SQLite、前端、Chromium、build/typecheck 和 diff。所有测试服务已停止；按明确名称及归属/无连接校验清理中断遗留的 5 个合成库，基础库和 Docker 卷保留，3001/8001/55432 不再监听。清理记录在 `cleanup-final.json`。

## 9. 剩余风险及 S5-2 前置条件

本机验收不代表生产就绪。尚未执行公网部署/托管数据库/TLS、生产 Worker 和解析进程资源隔离、正式私有存储/Windows ACL、持久运行多进程 BYOK、供应商数据政策与多样真实模型质量、正式灾难恢复/加密保留与删除重放、真实用户试点及长期容量观察；没有将这些写为通过。现有密码恢复、邮箱验证、数据导出和账户注销尚未实现。

S5-2 必须另外授权后再开始，先明确导出/注销产品确认、删除等待及最小记录保留规则，盘点 users/邀请/会话/限流、简历/操作记录/私有对象、旧事实和正式画像/候选、运行/计量/BYOK、岗位/JD/条件/匹配/回执、申请/行动/事件/墓碑，以及缓存/相关任务。设计撤销会话、阻止并发新写/迟到任务、可重试幂等清理与导出隔离；备份保留和恢复后删除重放需与 S5-3 共同确定。本轮不提前实现任何注销/导出接口。
