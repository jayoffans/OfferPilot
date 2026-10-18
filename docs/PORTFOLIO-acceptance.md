# GitHub 作品集交付验收

日期：2026-10-18（Asia/Shanghai）。目标为 GitHub 展示、简历介绍与技术面试，完成本地作品交付整理。没有公网部署、购买服务器、域名操作、真实付费 DeepSeek 请求、GitHub 推送或 Git 历史重写。本轮没有创建提交，全部内容留在工作树供维护者审阅。

**收口结论：本轮作品集整理及本机验证完成。** SQLite 598项、真实PostgreSQL原API/领域288项与专项53项、前端140项、最终Chromium36项通过；契约、类型、lint、构建、HTTP、截图清单与文档链接检查通过。远程CI尚未执行，许可证及历史身份/路径公开范围仍需维护者在发布前确认。

## 1. 仓库与分支核对

| 项目 | 实际结果 |
|---|---|
| 开发分支 | `codex/s5-1-postgresql` |
| 当前 HEAD | `96709a6ffbd53db5c30d7000a80daa4679db0dea` |
| 远程 | [jayoffans/OfferPilot](https://github.com/jayoffans/OfferPilot) |
| GitHub 默认分支 | `git ls-remote --symref origin HEAD` 核实为 `main`，HEAD=`ca9db6f8fc4cf43aa50e049b075493c2f7ac7769`，仍为初始化提交 |
| 本地 main | 与 `origin/main` 相同，未切换、合并或改写 |
| 当前 HEAD 对 origin/main | 领先 13 个提交、落后 0 个；S5-2 和本轮仍未提交，不包含在 13 个中 |
| 尚未存在于 origin 跟踪分支的提交 | 11 个，列表如下；远程另有 `feature/resume-agent`=`9ec576d`，与本地跟踪 ref 一致 |
| 初始工作树 | 已有 S5-2 业务代码、0010、前端页面/契约、测试和两份验收/清单文档未提交；保留并在本轮回归中验证 |

```text
96709a6 PostgreSQL adapter and reliability verification
0526b7b accepted S4-2 workbench MVP
237272a manual application tracking and persistent timeline
290a988 accepted S3-2 trusted job matching
2f44f2f private job import and reviewed JD requirements
de9f204 accepted S2-2B single-sample evaluation
de9040d single-sample PDF evaluation through trusted workflow
aad33bc S2-2B integration and free model readiness review
78aecf8 S2-2A extraction pipeline
d915824 S2-1 trusted career profile
571d95d S1-1 / S1-2 / S1-3 baseline
```

未执行 fetch 改动远程跟踪分支；通过只读 `ls-remote` 核对远程实际默认分支和 heads。发布前如远程状态变化需重新比较。当前 `git diff` 同时包含原 S5-2 和本轮整理，不能把全部差异归为本轮新增业务功能。

## 2. 整理内容与 README 结构

保留 S1～S5-2 的身份、文件、候选/画像、模型适配、岗位、匹配、投递、工作台、PG 与数据生命周期功能。本轮没有更改领域代码或数据库结构。

README 现在按“介绍与真实问题 → 核心功能 → 系统截图 → 技术栈/当前架构 → AI/证据 → 可信匹配 → 持久任务与恢复 → 认证隔离 → 本地启动 → 测试/CI → 限制与后续 → 仓库/许可”组织。默认中文，没有用户量、生产性能、部署经历或总体模型准确率声明。

新增文档索引、当前架构导读、本地运行、测试说明、演示和面试材料，以及贡献、安全、第三方许可说明和 GitHub Issue/PR 模板。原 README、PLAN、roadmap 保存为三个历史存档；README 存档链接适配新位置，旧停止点明确为历史。PRD/架构/数据库同步当前本地作品集边界，取消云部署、域名备案、大规模容量、多节点高可用、复杂生产监控、商业化隐私政策建设和线上试点。

旧文档中明确的开发机用户目录改为占位符，未改其版本、结果和问题记录。另澄清 S5-2 表计数：实际为 23 张 ORM 业务/支撑表加 Alembic 版本表，共 24 张；此前“22 张既有”包含版本表，不能再与新增两张合称 24 张业务表。

## 3. 本地启动与演示数据

复用固定镜像摘要的 PostgreSQL Compose，只运行一个数据库容器、只监听 loopback。FastAPI 和 Next.js 本机进程保留直接调试入口；全套容器化会增加打包、迁移和多进程调试步骤，本轮未引入。

新增 `scripts/local/manage.py`：`init / migrate / seed / api / web / status`，`--profile` 隔离不同演示环境。新 PostgreSQL 数据库随机命名；迁移使用 `s5_migrator`，运行使用受限 `s5_app`；SQLite 作为无需 Docker 的替代。Alembic 升级到0010，独立删除清单仅在新环境初始化。已有 `.env`、开发库、Docker 密码和卷保留。

合成种子通过正式 FastAPI API/用例创建：合成账户、手工正式画像、三个职位及已审核条件、三个规则匹配、三个申请与相对播种日的行动、一份登记合成 PDF 和七个明确 Mock 的 pending 候选。重复播种不增加记录；无真实 Key、模型请求或实际申请。随机登录密码只在忽略的 `.local/<profile>/demo-credentials.json` 中，输出只提示路径。

实际验证全新 SQLite 和全新 PostgreSQL 的初始化、迁移、播种、重复播种及状态检查，全部通过；真实 `uv run --locked --project backend` 入口也执行通过。进一步实际启动 `manage.py api/web`，验证 `/health`、`/login`、Next 同源代理登录和工作台三条申请，随后关闭自建进程。8000/3000/8001/3001 在收口检查无监听。合成环境留在被忽略的 `.local/`，数据库容器与原有卷保留，便于维护者继续体验。

完整命令与环境变量见 [LOCAL_SETUP](LOCAL_SETUP.md)。

## 4. 演示截图

本轮重新运行完整 Chromium 后收录 **11 张 PNG，合计 1,721,904 字节**：

| 文件（位于 `docs/images/demo/`） | 页面 |
|---|---|
| `login.png` | 未填写凭据的登录页 |
| `workbench.png` | 合成申请统计与今日行动 |
| `resumes.png` | 合成简历详情与文本解析 |
| `candidates-mock.png` | 明确 Mock 的候选、字段归属及服务器证据定位 |
| `profile.png` | 用户手工保存的正式画像 |
| `jobs.png` | 用户私有职位列表 |
| `jd-review.png` | 免费规则整理后的条件审核 |
| `matching.png` | 确定性匹配及双侧证据 |
| `application.png` | 申请详情、下一步及时间线 |
| `export.png` | 本人 ZIP 导出就绪，密码框为空 |
| `workbench-mobile.png` | 390px 工作台 |

11 张均为真实页面原图，无图像生成或改图，账户全部为合成 `example.com` 身份。逐张原分辨率审阅，Key/密码为空、Mock 来源可见；PNG 无文本/EXIF 元数据。固定来源、测试和 SHA-256 见 [manifest](images/demo/manifest.json)，复制脚本和演示路线见 [DEMO](DEMO.md)。不能将候选图解释成真实 DeepSeek 结果。

## 5. 本轮验证

环境：Windows build 26200、Python 3.11.5、uv 0.12.19、Node.js 22.16.0、npm 10.9.2、Next.js 16.4.0、Playwright 1.64.0、Chromium、本机 Docker Engine 29.8.1 / PostgreSQL 18.6。两端使用现有锁文件；所有数据库和用户资料为隔离合成数据。并行运行部分验证，耗时不能作为性能基准。

| 检查 | 本轮实际结果 |
|---|---|
| 后端 unittest / SQLite | 发现651项，**598通过、0失败/错误、53项PG opt-in跳过**，717.467秒，exit=0 |
| 真实 PostgreSQL 专项 | **53/53通过**，96.579秒；新增跨平台封装入口另行实际执行 **53/53通过**，89.901秒，不累加为106项独立测试 |
| 真实 PostgreSQL 原 API/领域回归 | **288/288通过**，0失败/错误/跳过，723.981秒，exit=0；日志按测试ID去重仍为288项，与专项合计341项 |
| 前端 Vitest | **140/140通过，13文件**，26.57秒，exit=0 |
| 完整 Chromium / SQLite | 初次 **36/36通过**，5.2分钟；平台路径与上传导航等待修复后最终全套 **36/36通过**，4.9分钟，均零重试、exit=0 |
| CI 平台路径修复后的账户 E2E | 首次单独复验2通过/1超时；发现上传完成后导航未稳定的测试竞态，修复公共助手后3个账户场景及其余33项均通过，见上行最终全套 |
| 真实 HTTP 文件生命周期 | 通过，exit=0 |
| 真实 HTTP 导出/注销生命周期 | 通过，exit=0 |
| OpenAPI 契约 / ESLint | 通过，exit=0；共享E2E助手修复后ESLint再次通过 |
| Next production build / TypeScript | 顺序执行均通过，exit=0；测试助手修复后TypeScript再次通过；未部署 |
| 锁文件 / 新脚本语法 / 截图清单 | `uv lock --check --offline`、新Python入口AST、11图哈希/宽高检查通过 |
| Git diff / 文档链接 | `git diff --check`通过；根目录及docs共40份Markdown的本地目标检查无断链 |

未为文档整理新增追求数量的测试。原安全断言、故障注入、真实 PG 连接与 Chromium 场景保留；SQLite 跳过不算 PG 成绩。此次没有重跑 PG Chromium、100用户查询基准或完整备份/删除重放演练；既有入口和历史证据保留在 S5-1/S5-2，本轮主要变化为文档、工具与CI。

恢复执行时，上一组 PG 全回归尚未生成汇总便中断，不计其部分日志为完成成绩；11:13 的首次续跑又因 Docker Desktop 已关闭而未进入测试。启动本机 Docker 后重新完整执行，以最终 runner 退出码和 JSON 汇总为准，不累加中断次数。

CI 平台审查另外发现两处遗留 Windows 路径假设：账户 E2E 的 ZIP 检查固定使用 `Scripts/python.exe`，HTTP smoke 总是使用基础解释器且只补 Windows venv 依赖。已按平台选择 venv 解释器，保持 Windows 既有处理；两项真实 HTTP smoke 在修复后再次通过。修改只涉及测试启动工具，未改变业务断言。Ubuntu 的实际执行仍待远程 CI，不把本机复验称为 Linux 验收。

上述账户E2E首次复验的失败发生在清理故障注入之前：上传弹窗已关闭，但异步`router.push`尚未稳定，测试打开旧页面账户菜单后，节点被详情页导航卸载。失败快照仍为正常简历详情，没有清理业务错误。公共`uploadUI`现等待正确详情URL及文件标题再返回；没有加睡眠、放宽超时或跳过断言。为覆盖所有调用者，修改后重跑完整36项浏览器套件，而不只挑失败项。

本机详细日志保存在忽略的 `frontend/artifacts/portfolio/` 及 `frontend/artifacts/portfolio-*.log`，不是其他人克隆后已有的成果。后端完整日志为 `portfolio/backend-tests.log`，浏览器初次为 `portfolio/chromium-e2e.log`，最终为 `portfolio/chromium-e2e-final.log`；PG 原 API 汇总由 runner 写 `portfolio/pg-regression.json`。原始验收全部保留，不用历史数字替代本表。

## 6. CI 交付与实际边界

[ci.yml](../.github/workflows/ci.yml) 配置三个 Ubuntu 任务：后端/SQLite/HTTP/合成种子与截图清单，真实 PostgreSQL 专项/API回归/种子，前端契约/lint/unit/build/types/Chromium。Actions 固定已核验SHA，权限只读，checkout不保留凭据，设置超时和同ref取消；不配置Key，不调用真实付费DeepSeek，没有部署/发布步骤。

**GitHub 托管 CI 尚未执行：本轮没有推送。** 本机对应入口已执行，Linux 首次安装、Docker/Chromium 依赖及网络仍需实际远程运行确认。CI 的浏览器默认使用 SQLite，PG 浏览器有明确的本地 opt-in 命令，未宣称远程 PG E2E 已通过。完整入口见 [TESTING](TESTING.md)。

## 7. 安全及许可证审查

扫描全部本地 refs 可达的 **14个提交、556个唯一blob**、提交消息和可发布工作树，而非只看当前文件。未发现可确认的真实 Key、口令、私钥或真实求职者资料误提交。规则候选核对为测试/占位/变量/合成内容；唯一历史PDF与登记合成工厂字节一致。两份本机环境文件含非空Key/数据库密码，但被忽略且未进入可达历史，不输出其值。

历史仍含两个非noreply Git作者邮箱和本机目录信息；当前文档已脱敏，**旧历史未改写**。普通分支不会携带本机reflog/不可达对象；本次没有把完整`.git`归档作为发布物。没有进行依赖漏洞扫描或声称扫描可识别所有个人信息。详细范围、规则、验证及未来发现历史秘密的安全处理方案见 [SECURITY_REVIEW](SECURITY_REVIEW.md)。

`.gitignore` 补保护本地演示、数据库、PDF、日志、ZIP、备份与私钥，仅放行登记合成PDF和已审阅截图。没有引入新运行依赖。项目自身尚无LICENSE；PyMuPDF是AGPLv3/商业双许可，另有psycopg LGPL、原生图像库及MPL/CC-BY等传递依赖告知需保留。[第三方许可审查](THIRD_PARTY_LICENSES.md)列出已核对版本和发布路线，本轮未购买商业许可，也未擅自替维护者确定项目许可。

## 8. 面试材料与发布前确认

[INTERVIEW](INTERVIEW.md)覆盖背景和实际职责、FastAPI/Next选型、AI证据、规则匹配、隔离、幂等并发、SQLite/PG差异、困难问题、限制和2～3分钟介绍稿。引用真实代码/验收，明确AI协作与个人贡献须本人核对；没有虚构独立完成、生产运维、模型准确率或用户成果。

维护者审阅后确认以下事项，再另行决定是否提交/推送：

1. **许可与贡献权利：** 项目LICENSE、PyMuPDF AGPL相容发布或其他适用路线，以及第三方告知方式。
2. **历史公开范围：** 是否接受现有Git作者邮箱和本机路径披露；如需清理，先批准隔离副本中的具体历史修复方案，本轮未操作。
3. **提交边界：** 先保存已完成S5-2，再单独提交作品集整理，还是采用其他审阅后的拆分；不要把既有功能误归为本轮新增。
4. **远程整合方式：** 保留当前分支历史、通过PR合入main的方式、Actions运行权限与分支保护；远程main目前仍是初始化版本。
5. **简历/面试表述：** 填写真实个人职责，确认协作方式和可现场解释的技术内容；不使用稿件中的占位符作为已证实经历。

这些是发布前待维护者决定的事项，不代表已获推送许可。本轮完成后停止，等待审阅。
