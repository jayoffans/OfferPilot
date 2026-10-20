# GitHub 公开展示前最终检查表

> 最新发布状态以 [GITHUB-RELEASE-READY](GITHUB-RELEASE-READY.md) 为准。负责人已确认原创内容授权来源，正式采用 AGPL-3.0-only，并接受普通历史路径及完整历史。本文其余候选状态、计数和待确认项是旧轮次快照；远程操作步骤仅供未来明确授权后参考。

> 本文保存上一轮本地整理快照。最新的独立复核、许可证候选、路径脱敏、测试及发布阻塞见 [GITHUB-FINAL-RELEASE-REVIEW](GITHUB-FINAL-RELEASE-REVIEW.md)。下文提交数和“未准备许可文件”是当时状态；本轮只准备候选，未最终授权、未推送、未改写历史。

日期：2026-10-18（Asia/Shanghai）。用途是 GitHub 技术作品展示和本地面试演示，无需公网部署。本轮获准本地修改、测试及提交；**没有 push、修改远程 main、创建 Release、云部署、付费模型调用或历史重写。**

## 1. 结论与停止点

**本地技术准备已完成；公开发布仍需负责人确认许可证、历史身份/路径公开范围及推送授权。** GitHub 托管 CI 尚未执行，不能宣称远程全绿。没有发现必须阻断本地整理的已确认秘密或明显功能缺陷。

完成以下本地提交后停止。后续即使获得普通推送授权，也不包含强推、远程历史清理或云端部署授权。

## 2. 提交整理结果

| 本地提交 | 内容与边界 | 审查结果 |
|---|---|---|
| `293b29e` — `feat: add verified account export and deletion lifecycle` | S5-2 导出/注销领域服务、0010 向前迁移、会话/迟到写入围栏、中文页面、生成契约、SQLite/PG/HTTP/浏览器测试及两份验收文档；51 个文件 | 保留既有 S1～S5-1，0001～0009 未改；没有新增运行依赖或真实数据 |
| `d2c0e82` — `docs: prepare reproducible GitHub portfolio and local CI` | README、历史存档、架构/本地启动/演示/面试文档、11 张合成截图、隔离种子、CI 与贡献模板、扫描工具和忽略规则；48 个文件 | 截图逐张复核；原开发文档和验收保留，未将本机运行产物加入 Git |
| 本文件所属提交 — `chore: finalize safe branch CI and GitHub release review` | 功能分支 CI 触发、PostgreSQL 健康等待上限与安全诊断、最终审查文档和当前状态同步 | 与作品集初稿分开追加，不改写前两个提交；具体 SHA 用 `git log -3 --oneline` 核对 |

原工作树混合的 PRD/架构/数据库说明通过暂存区拆分，工作树内容未被替换或丢弃。原 13 个开发提交全部保留，没有 reset、rebase、amend、squash 或强制推送。每个新增提交均检查暂存文件清单、diff 和空白；归档中原 Markdown 双空格换行改为等效 `<br>`，记录内容不变。

## 3. 分支与真实远程差异

| 项目 | 核查结果 |
|---|---|
| 当前分支 | `codex/s5-1-postgresql`；本轮沿用原分支 |
| 初始 HEAD | `96709a6ffbd53db5c30d7000a80daa4679db0dea` |
| 初始差异 | 相对本地 `origin/main` 领先 13、落后 0；其中 11 个不在任何 origin 跟踪分支中 |
| 整理后差异 | 追加 3 个本地提交，相对 `origin/main` 领先 16、落后 0；14 个提交尚不在 origin 跟踪分支中 |
| 远程仓库 | [jayoffans/OfferPilot](https://github.com/jayoffans/OfferPilot) |
| 只读远程核查 | `git ls-remote --symref origin HEAD` / `--heads origin`：默认 `main`，main=`ca9db6f8fc4cf43aa50e049b075493c2f7ac7769`；另有 feature/resume-agent=`9ec576d06867a3d44eeaa255e03302f80d9faf8e`，与本地跟踪 ref 一致 |
| main 与远程 | 本地 main、origin 配置与远程 heads 未修改；当前功能分支没有设置 upstream |

这些是本轮读到的远程状态，不保证授权发布时没有新提交。发布前重新 fetch 并比较；如 main 已变化，先审查差异并决定普通合并方案，不能默认强推。最终工作树应为空，私有 `.local/`、`.env*` 和测试证据继续被忽略。

## 4. 许可证推荐与待确认

实际锁定 **PyMuPDF 1.28.2**，正式 PDF 校验/提取和评测、种子、HTTP/PG/E2E PDF 生成均直接依赖它。[上游锁定版本 COPYING](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING)和[官方文档](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)确认 AGPLv3 / Artifex 商业双许可；不是可用项目 MIT 标签覆盖的普通 MIT 依赖。

| 方案 | 合规风险 | 修改成本与作品展示适配 | 后续影响 |
|---|---|---|---|
| A 保留 PyMuPDF，AGPLv3 相容发布 | 要确认自有代码授权权利、组合范围、第三方相容性和告知/对应源码义务；不认定所有文件必然同一法律作品 | 推荐优先确认；相对成本低，保留已验收的中文解析和证据行为 | 后续分发/贡献遵循选定条款；二进制、镜像或网络服务另核实际义务 |
| B 自有代码希望采用 MIT 等宽松许可 | 无适用商业授权时先移除 PyMuPDF/MuPDF，再核新依赖及传递许可；未来 MIT 不自动重授旧历史 | 中等工程批次：解析和 PDF 工厂一起替换，重验中文/页段/错误/资源限制/Mock与浏览器；pypdf、pdfminer.six 仅为候选 | 有利宽松复用，但需维护新旧授权边界；替换会影响文本顺序、偏移与资源版本 |
| C 商业授权 | 必须取得覆盖具体主体、公开/分发方式及未来用途的书面合同；其他第三方义务仍在 | 当前不优先推荐；代码改动少，但报价/合同成本未确定，个人公开源码作品集收益有限 | 未来确有闭源交付需求时再询价，按合同范围维护 |

**推荐 A，决定权归项目权利人。** 当前未添加 LICENSE、购买授权或宣称完整法律合规。接受 A 后，仍要确认具体版本表述、版权/贡献归属、源码和告知覆盖范围，再补齐许可文件；若必须 MIT，先另行授权 B 的替换批次并暂缓许可放行。完整依据、第三方依赖矩阵及验收边界见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.md)、[Artifex 官方说明](https://artifex.com/licensing)（访问：2026-10-18）。

## 5. Git 历史安全审查

初始扫描覆盖全部本地 commit refs 的 **14 个提交、556 个唯一 blob**，以及额外 **10 个非 commit checkpoint tree refs 的 97 个独有 blob**，共 653 个。扫描还覆盖提交消息和 Git 允许加入的当前工作树；并非只检查 HEAD。新增提交完成后复跑同一入口，最终结果保存在本机忽略目录，公开文档保留初始基线，避免自引用计数漂移。

前两个提交后复扫为16个提交、703个唯一blob、101条规则候选；比初始98条多3条，是已审阅的环境示例和合成HTTP/E2E口令进入新历史版本，表达式与旧版本一致。另6条只从checkpoint范围转入提交范围。未发现新的真实秘密，新增文档和脚本没有新规则候选。最终本地提交完成后再次扫描并比较此报告，不以候选数量直接决定放行。

| 类别 | 结果与处置 |
|---|---|
| API Key / Token / 密码 / 私钥 | 未发现可确认误提交的真实凭据。候选人工核对为合成测试、占位、环境插值、URL 反例或界面属性；不能将模式匹配等同真实泄漏 |
| `.env` / 私有配置 | 两份本机配置含非空真实设置，均被忽略，未见进入可达 refs；不输出值、不加入 CI |
| 数据库 / ZIP / 缓存 / 日志 / 备份 | 未发现加入可达 refs 的运行库、导出 ZIP 或私钥；工作区运行产物继续忽略，不打包整个 `.git` 或 artifacts |
| 简历 | 唯一历史 PDF 是登记合成夹具，2081 字节/2 页，与合成工厂字节及 SHA-256 一致；不含真实求职者履历 |
| 本地绝对路径 | 旧提交含用户目录；4 份历史评测 JSON 仍含原报告/合成 PDF 的仓库绝对路径。属于开发环境信息，无认证意义；负责人需接受其公开或另行决定处理方案，不能称为“没有绝对路径” |
| 作者/提交者邮箱 | `codex@localhost`、`local-checkpoint@offerpilot.invalid` 为开发占位身份，不是已确认真实个人邮箱；会公开协作身份且不会自动关联本人 GitHub 贡献 |
| 图片信息与元数据 | 11 张真实页面 PNG 逐张复核，全部合成；密码/Key 未填写，无 Cookie/真实 ZIP/私有路径；仅含 IHDR/IDAT/IEND，manifest 哈希通过。保留已审阅快照，不因 E2E 重跑自动覆盖公开图 |

未来提交建议使用 **GitHub Settings → Emails 中本人实际显示的 noreply 邮箱**，可先设置仓库本地 `user.name` / `user.email`；不要猜测账户数字 ID。本轮沿用已有占位作者配置，没有擅自改变身份或重写历史。

普通单分支 push 只传播该分支可达提交，不会自动发布其他分支、`refs/codex/...` tree refs、reflog 或不可达对象；禁止为方便使用 `--all` / `--mirror`。若以后确认真实凭据已经泄漏，必须先撤销/轮换，再评估缓存与历史清理；只删除当前文件不足以解决泄漏。本轮没有需要立即轮换的已提交凭据证据。

扫描是启发式加人工复核，非完整 DLP/熵扫描/依赖漏洞审计；不保证发现混淆秘密或任意姓名/地址，没有 OCR、远程独有对象及外部备份审查。范围、误报分类和扫描器 7 项隔离回归见 [SECURITY_REVIEW](SECURITY_REVIEW.md)。

## 6. 面试官视角检查

| 检查 | 结论 |
|---|---|
| 30 秒理解项目/首屏价值 | 标题后直接说明中文求职工作台、证据与人工确认；给出问题、闭环和本地定位 |
| 真实页面截图 | README 默认显示工作台，其余页面可展开；DEMO 有 11 张合成原图、路线和 manifest |
| 技术栈与架构 | 与锁文件和当前模块化单体一致；没有将 Redis/Celery、云、Browser 或生产运维写成成果 |
| 核心代码入口 | ARCHITECTURE_GUIDE / INTERVIEW 链接服务、Schema、Adapter、事务/任务、授权与验证文件 |
| 本地运行 | 锁文件、跨平台工具、独立 profile、迁移/运行角色、种子及排障完整；无 Key 也能完成手工闭环 |
| Mock / 真实 DeepSeek | 截图持续标 Mock；真实仅有注明日期的单份合成样本记录，未推断总体准确率 |
| 规模/效果与测试环境 | 无真实用户规模、总体正确率或生产性能夸大；Windows 本机与未来 Ubuntu CI 分开记录 |
| 工程能力与贡献 | 有领域边界、版本化契约、证据校验、幂等/租约、隔离、文件补偿、PG 并发及故障测试；个人职责和 AI 协作范围需本人核对 |

未为美化页面增加新框架、功能或虚假数据。当前没有明显阻碍面试演示的问题；邀请注册、文本 PDF、单进程 BYOK、规则语义和逻辑删除限制已说明。介绍稿仍有个人职责占位符，使用前应按实际经历填写。

## 7. 本轮本地验证

环境：Windows build 26200、Python 3.11.5、uv 0.12.19、Node 22.16.0、npm 10.9.2、Next 16.4.0、Playwright 1.64.0、Chromium、Docker Engine 29.8.1 / PostgreSQL 18.6。复用锁定依赖，数据均为隔离合成内容，不表示完成全新 Ubuntu 安装或生产容量验收。

| 验证 | 本轮结果 |
|---|---|
| 后端 unittest / SQLite | 运行651项，598项通过、53项显式跳过（PG opt-in）；无失败，PG另行执行不计为跳过通过 |
| PostgreSQL 专项及原 API/领域回归 | 专项53/53、原API/领域288/288通过，runner退出0；本轮重新生成摘要，非复用旧JSON |
| 真实 HTTP 文件 / 账户生命周期 | 两项 smoke 均通过 |
| 前端 Vitest | 13 个文件、140 项通过 |
| OpenAPI / ESLint / build / TypeScript | 全部通过；build 与 E2E 按顺序运行 |
| Chromium | 36/36 通过（SQLite、真实 API/Web、免费模型夹具）；进程与临时库按现有隔离器结束清理 |
| 全新 SQLite 合成环境 | init、seed、重复 seed、status 通过；原环境未覆盖 |
| 全新 PostgreSQL 合成环境 | init、0010 迁移、seed、status 通过；实际使用受限运行角色，未覆盖原数据库或卷 |
| CI 静态审查 | 三任务 YAML、固定官方 Action SHA、只读权限、超时、锁文件和解释器路径通过；uv offline 锁检查、入口 AST 与 shell LF 通过 |
| 文档 / 截图 / 暂存检查 | 412 个相对文件链接无缺失、11 张截图 manifest 通过；新增提交逐项检查暂存文件清单及空白 |

本轮不重跑历史 100 用户查询基准、PG Chromium、完整备份/删除重放演练或真实模型评测；保留 S5-1/S5-2 原验证入口和实际证据。没有后端独立 lint/typecheck 命令，不虚报这些检查。当前 Starlette TestClient 兼容性弃用提示仍存在，但相关 API/HTTP 测试需以真实返回和退出码为准。

新增提交的空白检查通过；全分支对 main 的 `diff --check` 另提示原 S2-1 Markdown 双空格换行、`matching.spec.ts` 和 `test_extraction_runs.py` 原有文件末尾空行。它们不是运行或安全缺陷，本轮未为消除旧格式告警改动业务/测试文件或重写历史。

详细日志在忽略的 `frontend/artifacts/release-review/`、`frontend/artifacts/portfolio-release-vitest.log`；PG runner 当前摘要为忽略的 `frontend/artifacts/portfolio/pg-regression.json`。他人克隆后不会自动拥有本机日志或私有演示环境。

## 8. GitHub Actions 审查与修复

- SQLite：锁定后端依赖，完整单元/集成、HTTP 生命周期、全新合成种子及截图 manifest。
- PostgreSQL：固定镜像摘要，仅 loopback；随机本机/runner 凭据、迁移/业务角色分离、每用例新库及清理；保留卷，健康等待新增 90 秒上限。失败只输出 `compose ps --all`，不输出配置、密码或数据库日志。
- 前端：npm 锁文件与缓存键、OpenAPI/lint/Vitest/build/types，随后 `playwright install --with-deps chromium` 和 E2E。浏览器默认 SQLite，真实 DeepSeek 场景注入免费 Provider。
- 平台：Python 解释器按 Windows/POSIX 分支；Docker shell 固定 LF，配置路径用 Path；没有宣称 Windows 实测等于 Linux 通过。
- 权限与额度：`contents: read`、checkout 不保留凭据，Actions 固定官方 SHA，任务 20/30/30 分钟上限，同 ref 取消旧运行；不使用仓库秘密、不执行付费 AI、部署或发布。
- **修复首次触发：** push 由原 main/master 扩至 `main/master/codex/**`，功能分支首次推送即可触发。workflow_dispatch 在默认分支尚无工作流时不能作为首次入口；PR 也会触发。

本地静态检查和测试不能代替 GitHub 托管 runner。网络安装、Linux 原生包、Docker/Chromium 系统依赖及 GitHub 权限，必须观察首次真实 Actions 才能放行。完整入口见 [TESTING](TESTING.md)。

## 9. 仍需负责人确认

- [ ] 接受 A 的 AGPLv3 相容路线及其实际授权范围，确认贡献/素材权利并补齐 LICENSE/告知；或另行授权 B/C。
- [ ] 接受历史占位邮箱、本机路径和协作记录公开；如要求历史清理，先明确批准隔离副本方案，当前不自动执行。
- [ ] 确认未来提交的本人显示名及 GitHub noreply 邮箱；核对面试中的个人职责与 AI 协作表述。
- [ ] 明确授权普通推送当前功能分支、运行 Actions 和创建 PR；当前尚无此授权。
- [ ] 首次 Actions 全部通过后，再授权/亲自通过 PR 合入 main；选择保留原提交历史的 merge commit。

数据保留/供应商条件中与未来公网服务相关的事项不作为本地展示虚假承诺；本轮不启用真实个人数据或默认收费调用。未来服务化需另行审查。

## 10. 远程发布操作顺序（仅计划，本轮未执行）

1. 先完成上节许可和身份决定，将确认后的 LICENSE/必要告知作为普通新提交，重新扫描与验证，不改旧提交。
2. 授权后读取最新远程状态，审阅待公开 diff、提交历史、文件及范围：

   ```sh
   git status --short
   git branch --show-current
   git fetch origin
   git rev-list --left-right --count origin/main...HEAD
   git merge-base --is-ancestor origin/main HEAD
   git log --oneline origin/main..HEAD
   git diff --stat origin/main...HEAD
   uv run --locked --project backend python scripts/security_scan.py --output frontend/artifacts/security/pre-push.json
   ```

   先确认处于待发布的 `codex/s5-1-postgresql`。`merge-base` 非零或存在新远程提交时先复核，不能直接覆盖远程。不要将开发路径误报为密码，也不能忽略真实凭据候选。
3. 仅在明确推送授权后执行普通功能分支推送（不推 main、不带 `--force` / `--all` / `--mirror`）：

   ```sh
   git push --set-upstream origin HEAD:refs/heads/codex/s5-1-postgresql
   ```

4. 查看该 commit 的真实三组 Actions 结果。若失败，本地复现、追加修复提交、普通推送；必要时人工重跑，不能写“已通过”掩盖失败。
5. 许可与 CI 放行后创建 base=main 的 PR，说明 S1～S5-2、测试环境、限制及本次整理；关联该 PR。PR 再跑检查并审查公开 Files changed。
6. 经负责人授权或本人操作，以 **merge commit** 合入 main，保留完整开发记录，不用 squash/rebase 替换原历史。无需建立公开 Release 或部署。
7. 按下一节检查 GitHub 页面，最终面试链接才使用默认分支仓库地址：[OfferPilot](https://github.com/jayoffans/OfferPilot)。当前远程 main 仍为初始化版本，不应提前当作完整作品交付。

## 11. 发布后验证清单

- [ ] 默认 main 包含已审查的提交、LICENSE/第三方告知和最终文档。
- [ ] Actions 在所发布 SHA 上三组通过；没有取消/跳过被当作通过，无收费请求或发布步骤。
- [ ] README 首屏、Mermaid、展开截图、DEMO 和本地链接在 GitHub 正常显示；11 张图可打开，Mock 标识保留。
- [ ] 从全新目录 clone，仅按 LOCAL_SETUP 锁定安装，至少完成 SQLite 无 Key 体验；检查 Linux CI 和 PostgreSQL 结果。
- [ ] 本人登录、资料/职位/四态匹配/投递/导出流程可复现；注销/隔离/错误边界依据测试记录讲解。
- [ ] 无真实配置、数据库、ZIP、Key、真实简历或测试 artifacts；下载页面/原图无敏感内容。
- [ ] 仓库介绍、主题与个人职责准确，最终简历/面试链接指向已合入的默认分支；不虚构用户数、模型准确率或部署经历。

本轮到本地整理为止，等待最终发布授权。
