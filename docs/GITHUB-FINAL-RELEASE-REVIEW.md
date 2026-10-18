# OfferPilot GitHub 正式发布前最终审核

日期：2026-10-18（Asia/Shanghai）。用途：GitHub 作品展示、本地复现、简历和技术面试。

**状态：本地发布候选，尚未放行公开推送。** 推荐继续采用 AGPLv3 相容方案；项目授权、贡献权利、适用告知和历史路径公开范围仍需负责人确认。本轮只审查、修改、测试和普通本地提交，没有 push、force push、远程 merge、PR、Release、部署、收费模型请求或历史改写。

本报告基于重新读取真实仓库和 Git 对象，不以此前报告替代验证。旧验收记录保留为历史；本报告中的本轮数字与旧轮次不累加。

## 1. 实际 Git 状态与公开范围

| 项目 | 本轮初始核查 |
|---|---|
| 分支 | `codex/s5-1-postgresql`，没有 upstream |
| 初始 HEAD | `34a1c88bb2682b71178c9d6e5ae15bf5c0abae0c` |
| 最新三个提交 | `34a1c88` → `d2c0e82` → `293b29e`；此前提供的顺序与实际不符 |
| 初始工作树 | 干净 |
| 相对 `origin/main` | 领先 16、落后 0；HEAD 共 17 个提交 |
| 不在任一远程跟踪分支的提交 | 14 个 |
| main / origin/main | `ca9db6f8fc4cf43aa50e049b075493c2f7ac7769` |
| 只读远程 heads 核对 | main 仍为 `ca9db6f`，feature/resume-agent 为 `9ec576d06867a3d44eeaa255e03302f80d9faf8e`，与本地跟踪引用一致 |

本轮只用了 `git ls-remote --heads origin` 查询远程，没有 fetch、修改远程或调整跟踪引用。此结果不保证未来授权时远程没有新变化。

初始 14 个未推送提交按开发顺序为：`571d95d`、`d915824`、`78aecf8`、`aad33bc`、`de9040d`、`de9f204`、`2f44f2f`、`290a988`、`237272a`、`0526b7b`、`96709a6`、`293b29e`、`d2c0e82`、`34a1c88`。S1～S5-2 全部保留，没有 squash、rebase、amend 或重建仓库。

初始 HEAD 可达对象为 **17 commit / 305 tree / 667 唯一 blob**。全本地 refs 扫描为 **17 commit / 692 唯一 blob**，其中 **25 个 blob 仅由 9 个非 commit checkpoint tree refs 引用**；可跟踪工作树 351 个文件。额外盘点 Git 对象库的 **1,262 个对象（17 commit / 436 tree / 809 blob）**，对全部 809 个 blob 补做同类规则和本机已知凭据精确比对。多出的不可达对象和 checkpoint 不等于会随普通功能分支推送；未清理或发布它们。

普通的指定分支推送会公开该分支的完整可达历史，包括旧版文件和已删除内容；不能只审核 HEAD。禁止用 `--all`、`--mirror`、完整 `.git` 归档或未经审核的其他 refs 代替此计划。私有配置和运行文件也不属于发布物。

## 2. AGPLv3 结论与官方依据

**推荐：保留 PyMuPDF，按 AGPLv3 相容方式发布应用源码。** 本次没有发现已确认的相互排斥许可条款；这不是把所有依赖重新授权为 AGPL，也不是完整法律合规保证。自有代码权利及组合范围仍由负责人确认。

真实声明 `PyMuPDF>=1.27,<1.29`，锁定并安装 **1.28.2**。正式 `backend/src/offerpilot_api/services/resume.py` 直接导入 `pymupdf`，在同一 API 进程校验 PDF、加密状态、页数并提取文本；合成 PDF 工厂、种子及 HTTP/PG/浏览器验证亦使用它。它是业务必须的库调用，不是独立进程工具或仅测试依赖。提取结果的文本顺序、页段偏移和摘要又被证据校验依赖。不能因动态导入、未改库、由使用者安装或作品集用途就认定没有组合许可义务。

官方依据（本轮访问）：

- [PyMuPDF 许可与版权](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)：PyMuPDF / MuPDF 提供 AGPL 与商业双许可，页面标明覆盖 1.28.2。
- [1.28.2 官方 COPYING](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING)与[原始文本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING)：完整 GNU AGPLv3 正文。
- [GNU AGPLv3 正式文本](https://www.gnu.org/licenses/agpl-3.0.html)：本轮 GNU 主站抓取超时，正文来自以上官方 tag 的完整 COPYING；本机包仅核对版本、64 字节许可摘要及源码标识，未把超时说成成功访问或暗示本机包附有完整正文。
- [Artifex 许可说明](https://artifex.com/licensing)：开源与商业授权路径，不因非商业展示自动免除开源许可要求。

**明确的不一致：** 安装包 METADATA/短 COPYING 与官方许可页声明 AGPLv3/商业双许可，但安装源码以及[官方同 tag 的 `src/__init__.py`](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/src/__init__.py)另含 `GPL-3.0-only` SPDX 标识。GPLv3 与 AGPLv3 第 13 条允许相应组合，未据此发现 AGPL 方向的明显阻塞；原文件许可仍应保留，不能统一改标。若负责人要求消除上游标识解释的不确定性，应向 Artifex 确认，不能把本报告当成其书面答复。

已准备 [LICENSE.candidate](../LICENSE.candidate)，是未经改写的标准完整 AGPLv3 文本，34,520 字节，SHA-256 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`。**没有建立正式 `LICENSE`，没有在项目元数据中宣称授权已生效。** README 明确候选、第三方边界及作品集定位；没有增加“禁止商用”等与开源许可抵触的用途限制。AGPL-3.0-only / or-later 的最终表述、版权主体、贡献范围和生效决定尚由负责人确认，候选正文自身不完成这些决定。

## 3. 第三方依赖、素材与对应源码

完整审查、组合表达式及限制见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.md)，精确锁定版本及元数据见 [机器可读清单](DEPENDENCY-LICENSE-INVENTORY.json)，原文版权/许可证见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.txt)。清单覆盖 **55 个第三方 Python 包和 636 个 npm 锁条目**；计数包括开发工具、条件依赖和可选平台包，不代表应用运行时全部加载。原文告知不是对这些包的再许可。

| 依赖/组件 | 版本 | 许可与需要保留的边界 |
|---|---|---|
| PyMuPDF / 随包 MuPDF | 均为 1.28.2（本机实测） | AGPLv3 / 商业双许可；另有上游文件 GPLv3 标识不一致，保留原标识 |
| FastAPI / SQLAlchemy / Alembic | 0.141.1 / 2.1.0 / 1.20.0 | MIT，保留版权和许可 |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | MIT |
| OpenAI Python SDK | 2.54.0 | Apache-2.0；不等于免费使用真实供应商 API |
| python-dotenv / argon2-cffi | 1.2.3 / 25.1.0 | BSD-3-Clause / MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause；保留选项及原文，不把 OR 当 AND |
| psycopg / psycopg-binary | 3.3.3 | LGPL-3.0-only；原生捆绑组件另有许可，未来二进制分发须单独审查 |
| certifi / tqdm | 见完整锁清单 | MPL-2.0 / MPL-2.0 AND MIT，文件级义务不能抹除 |
| Next.js / React / React DOM | 16.4.0 / 19.3.0 / 19.3.0 | MIT；Next 内嵌组件另保留原文告知 |
| Radix Dialog / Dropdown | 1.2.0 / 2.1.25 | MIT |
| Lucide React / Feather 来源图标 | 1.55.0 | ISC；涉及 Feather 的 MIT 版权同时保留 |
| sharp / libvips 原生包 | 见平台锁清单 | Apache、LGPL 及组合条款，不能只写 sharp 的顶层许可 |
| lightningcss / axe-core | 见完整锁清单 | MPL-2.0 |
| caniuse-lite 数据 | 见完整锁清单 | CC-BY-4.0，保留署名和告知 |

没有发现当前锁条目缺失 license 元数据就被静默放行的情况。Windows 实际安装包不能证明未安装的 `uvloop`、Linux/macOS 原生包及全部 wheel 内部组件已经逐文件审计。`node_modules` 的 6 个 extraneous 残留不在锁清单中，不作为交付依赖或公开制品；CI 使用 `npm ci`。Python 运行/开发依赖通过 uv.lock 固定；构建引导的 `setuptools>=70` 尚为范围，不能声称完全可重复的逐字节制品构建。

素材核查：源码未附第三方照片、简历模板、系统字体文件或远程 Web 字体。UI 图标来自 Lucide，CSS 使用系统字体栈；11 张 PNG 来自合成测试的真实本地页面。合成 PDF 的 CJK 字体为非嵌入引用，没有打包本机系统字体。截图和 PDF 不因此把第三方商标/字体的权利转授给项目；详细来源和声明继续保留。

对应源码和发布义务：

1. 发布应用可修改源码、接口定义、迁移、锁文件、配置示例及生成/安装/运行脚本；本仓库已经包含这些材料及 LOCAL_SETUP。秘密、真实简历和数据库不属于应公开源码。
2. 保留第三方版权、许可、无担保声明及适用 NOTICE；项目新增许可不能覆盖 LGPL/MPL/署名条款。确认自有代码授权权利及修改说明后再使正式 LICENSE 生效。
3. 核对 AGPL §0/§5(d) 的适用交互界面法律告知；最终许可确定后，补版权、无担保、许可及实际对应源码的可见入口。本轮不提前用 UI 授予候选许可，该项仍未放行。
4. 本轮发布物限定源码 Git 仓库，不包含 `.venv`、node_modules、wheel、可执行程序、数据库镜像或预构建容器。若以后分发二进制/镜像，按实际制品重新核对 AGPL §6、LGPL、MPL 及捆绑原生库的告知、源码和替换等义务；清单或上游链接本身不足以代替履行。
5. 当前没有公网服务。如果以后运行适用 AGPL §13 的修改版本，应为远程交互用户提供实际运行版本的对应源码入口；普通仓库链接不能自动证明版本和源码完整性。

## 4. 作者身份、邮箱与历史私人路径

初始 17 个提交的 Author 与 Committer 完全对应，只有两类身份：

| 身份 | 涉及范围 | 风险/决定 |
|---|---|---|
| `Codex <codex@localhost>` | 初始 16 个提交（`ca9db6f` 至 `34a1c88`，除 `571d95d`）；本轮追加提交沿用此占位身份 | 不是发现的真实个人邮箱；无法据此归属负责人 GitHub 贡献。仍需接受以该身份展示的真实开发记录 |
| `OfferPilot Local Checkpoint <local-checkpoint@offerpilot.invalid>` | `571d95d` 的作者及提交者 | 本地占位身份，`.invalid` 不是真实联系域名；不擅自替换 |

初始提交正文/文件的规则扫描未发现非示例个人邮箱。新增第三方告知中的上游公开版权作者/邮箱须保留，不能当作项目维护者私邮删除；它们与自有 Git 身份分开审核。本轮未设置或猜测 GitHub noreply 邮箱，未修改全局 Git 配置。未来由负责人从 GitHub Emails 设置复制本人的准确 noreply 地址，并在仓库本地配置；新配置不会清除旧历史身份。

**确有不一定希望公开的历史环境信息：** 一个数字型 Windows 用户目录标识、解释器/工具路径、开发仓库盘符和私人工作目录习惯。报告不重复原用户名和完整本机路径。它们不是认证秘密，但可关联本机账户和开发环境。

| 内容 | 涉及文件/提交范围 | 当前处理 |
|---|---|---|
| Windows 用户目录 | 旧 `docs/PLAN.md`：`571d95d` 至 `293b29e` 的 12 个快照；旧单样本报告：`de9f204` 至 `293b29e` 的 7 个快照 | 后续文档已使用占位符，旧 blob 仍在；另有 checkpoint/不可达本机对象，普通指定分支不会自动携带后者 |
| 仓库绝对路径 | 旧 README、PLAN、S1-3、S2-2B 相关文档、旧环境示例；4 份单样本 launch/safety JSON 从 `de9f204` 起保留 | 文档此前已脱敏，本轮将剩余 4 份 JSON 改为仓库相对路径；历史仍存在 |
| 通用路径反例 | evaluation.md 的通用 private 示例、ownership 安全反例及环境示例 | 不含真实账户或真实简历，保留以说明安全边界，不为消除扫描候选删测试 |

## 5. 秘密、个人数据和媒体检查

本轮先运行 `scripts/security_scan.py`，读取全 refs 的历史树/唯一 blob、提交消息及可跟踪工作树；另盘点全部本机对象，核对 Git 身份、盘符路径、已知本机凭据、PDF 文本/元数据和每张公开图片。初始规则候选为：连接串 14、字面口令 62、用户目录 13、手机号形态 9；**98 条候选记录不是 98 个秘密**，重复历史版本和工作树会重复计数。

逐项脱敏定位复核结果：

- 未发现可确认误提交的 DeepSeek Key、GitHub Token、私钥、Fernet Key、Session Secret 或可用数据库口令。Provider Token/私钥形态无初始命中；口令/URL 候选为测试固定值、环境变量插值、示例及拒绝凭据 URL 的反例；数字命中为合成电话或依赖 URL/hash。
- `backend/.env` 存在非空 DeepSeek 配置，`.env.s5-postgres` 存在三类非空数据库密码；两份均被忽略、未进入计划公开历史。仅在本机内存中比对其值与全部本机 Git blob/计划公开提交元数据，无命中；未输出值或值的摘要，也未向供应商验证有效性。没有证据要求因 Git 泄漏立即轮换；若后续发现可用秘密，必须停止发布、先撤销或轮换。
- 全 refs 未发现运行数据库、导出 ZIP、日志、备份、私钥或真实 `.env` 被跟踪。忽略目录中大量测试数据库、截图、日志和私有演示凭据仍留本机，不能打包 artifacts、`.local` 或整个工作目录。
- HEAD 历史媒体共 **12 个不同 blob：11 张 PNG + 1 份合成 PDF**，未发现额外的历史图片、真实简历或字体文件。无 submodule/gitlink 或已跟踪符号链接。
- 合成 PDF 为 2,081 字节、2 页，SHA-256 `cd6e8940637829abaa911fd22689db0f43573a24328f50574300af2f36711567`，与登记的 `synthetic_pdf_bytes('normal_chinese')` 逐字节相同。提取文本、两页渲染、元数据、链接及嵌入文件已核查：作者是合成评测标识，联系方式为示例，没有私人路径、附件或外链。
- 11 张 PNG 已逐张可视审阅，且 manifest 的 hash/尺寸/字节数一致。登录/导出密码框为空、Key 框为空、账户为 example.com 合成身份、Mock 标记可见；PNG 只有 IHDR/IDAT/IEND，无文本或 EXIF 元数据。

新增候选文件复扫中，第三方告知的邮箱规则命中为上游许可/版权原文的 **147 处、68 个唯一公开地址**，已逐段核对来源，应保留署名。新增两条手机号形态记录分别来自清单与告知中的同一 SHA-256 片段，不是联系方式。没有新本机路径或凭据形态命中。原文共 384 段，全部指纹与原安装文件核对一致。

本机脱敏证据位于忽略的 `.local/final-review/`；测试日志位于忽略的 `frontend/artifacts/final-review/` 和 `frontend/artifacts/final-release-review/`。完整对象盘点是补查，不代表全部二进制或混淆数据都有语义 DLP 覆盖；规则不保证发现任意姓名、地址、编码秘密或扫描件，不检查外部备份/其他人的克隆。未执行联网密钥验证、OCR 或依赖漏洞扫描，不宣称零风险。

## 6. 历史方案比较与建议

| 方案 | 隐私 | 真实开发历史/面试展示 | 远程引用与实施影响 |
|---|---|---|---|
| A 保留原历史 | 负责人接受占位身份、数字用户目录及旧本机路径公开 | 完整保存 S1～S5-2、失败修复及验收演进，最符合当前偏好 | 本地追加提交，不更换既有 SHA；公开分支的祖先同样可读 |
| B 隔离副本定向改写 | 可移除指定作者字段/路径/blob，需精确列允许替换范围 | 可保留提交结构和说明，但受影响提交及后代 SHA 改变；引用和签名可能失效 | 本地 branches/checkpoints/tags/跟踪 refs、PR 链接及旧克隆需逐个分析；部分祖先已在远程，改写可能不再是普通快进，需要另行远程授权。旧缓存/克隆不会自动消失 |
| C 独立审查后的公开作品仓库 | 新仓库只含审阅快照，原私有开发目录不携带 | 功能可完整保留，但公众无法从新仓库直接核对完整原开发历史；必须诚实说明迁移 | 独立远程地址、Issues/星标/链接及未来同步策略需另定；本轮不创建新公开仓库 |

**建议先让负责人选择是否接受 A。** 没有已确认秘密使历史改写成为技术必需；若要求不公开旧用户名/路径，单独普通提交不足以清除它们，需要另外批准 B 的隔离副本方案，或选择 C。当前未获得接受公开或改写的最终决定，因此隐私门槛仍未放行。

B 的后续批准应明确：目标 refs、字段/路径替换表、已在远程的祖先范围、提交数/SHA/签名和文档引用影响、离线备份位置、复扫范围及旧克隆/远程清理责任。先在隔离副本形成前后比较，负责人审阅后再决定是否替换 refs；本轮没有运行 filter-repo、批量 rebase、reset --hard、强推或删除重建仓库。

就本轮已定位的真实本机信息而言，用户目录最早进入 `571d95d`，四份 JSON 路径进入 `de9f204`，均属于尚未在 origin 跟踪分支中的范围。若以后批准的定向改写严格限于这些未推送提交，并保留已推送祖先 `9ec576d` 等不变，新功能分支仍可普通推送，不需要先假定强推。若改全部作者身份或已公开祖先，才会扩大到远程历史及其他克隆；届时另审，不在本轮权限内。

## 7. 作品展示质量与 CI

README 首屏可快速理解产品价值、人工确认边界和本地作品定位。技术栈与实际代码/锁文件相符；AI 候选、证据词法校验、确定性四态匹配、持久任务和数据生命周期具有代码及验收入口。真实 DeepSeek 只引用既有的一次合成单样本，不把 Mock、规则检查或数量当作总体模型准确率。未发现虚构用户量、生产部署或独立完成全部工作的表述；面试中的个人职责仍须本人按真实参与填写。

11 张截图清晰、相对路径有效；长图可打开原图查看，390px 布局无明显遮挡。未重新设计 UI、增加功能或替换截图。已确认的展示缺口仅为许可决策与旧路径披露，本轮只做相应整理。

三任务 CI 静态审查：

| 任务/方面 | 核查结果 |
|---|---|
| SQLite | Ubuntu 24.04，根工作目录，锁定后端安装，完整单元/集成、真实 HTTP、合成种子、截图清单 |
| PostgreSQL | 固定 18.6-bookworm 镜像摘要，Compose loopback，独立迁移/受限运行角色，随机私有凭据，每用例独立库及清理；90 秒健康等待；失败仅 `ps --all` |
| 前端/Chromium | Python+uv、Node 22.16.0、npm ci 与锁缓存键；契约/lint/unit/build/typecheck 顺序；frontend 工作目录安装 Chromium `--with-deps`；E2E 和 build 不并写 .next |
| Linux | Python 平台解释器分支、Path 路径及 init.sh LF；Windows 本轮通过不等于 Linux 实测 |
| 权限/额度 | contents:read、checkout 不保留凭据；4 个 Action SHA 本轮与官方仓库标签只读复核；20/30/30 分钟超时，同 ref 取消 |
| Key/费用/日志 | 无仓库 secrets 引用和发布步骤；workflow 真实抽取默认关闭。E2E 为验 BYOK 显式开启界面路径，但固定替换 FreeDeepSeekFaultProvider；其他真实协议测试用 MockTransport/禁网桩，没有真实供应商请求。不输出配置/数据库日志或凭据 |

未发现必须修改工作流的明确缺陷。**远程 GitHub Actions 尚未执行，不能标为通过。** Linux 安装、原生包、Chromium 系统依赖及 GitHub runner 权限须在后续授权推送后观察真实结果。

## 8. 本轮免费验证

使用锁定环境和隔离合成数据，无收费供应商调用。环境为 Windows、Python 3.11.5、uv 0.12.19、Node 22.16.0、npm 10.9.2、Docker 29.8.1 / PostgreSQL 18.6。结果由本轮实际执行生成，未复用旧日志作为通过依据。

| 验证 | 本轮结果 |
|---|---|
| SQLite 后端 | 651 项运行：598 passed、53 skipped（PG opt-in）、0 failed/error；703.563 秒，exit 0 |
| PostgreSQL 专项 | 53 passed，0 failed/error/skipped；101.292 秒，exit 0 |
| PostgreSQL 原 API/领域回归 | 288 passed，0 failed/error/skipped；724.696 秒，runner exit 0 |
| 前端 Vitest | 13 文件，140 passed，0 failed/skipped |
| Chromium E2E | 36 passed，0 failed/skipped，零重试，5.4 分钟，exit 0；SQLite 独立真实 API/Web |
| OpenAPI / TypeScript / ESLint | 均通过，exit 0 |
| Next.js 生产构建 | 通过，12/12 静态页面，exit 0；没有部署 |
| 真实 HTTP 文件/账户生命周期 | 各 1/1 通过，exit 0 |
| 全新 SQLite / PostgreSQL 本地流程 | 两套 init→seed→重复 seed→status 均通过；真实 loopback API health/login/session/dashboard（3 申请）/logout/撤销与关闭通过 |
| 正式本地 Web/同源代理 | manage.py api/web、health、登录、session、3 条合成申请、CSRF 退出均通过；自建服务已停止 |
| 依赖/平台入口 | uv sync --locked --offline、uv lock --check --offline、uv pip check、npm ls 主依赖；5 个 CI/启动 Python 入口 AST、init.sh LF 通过 |
| 图片/相对链接 | 11 张 PNG manifest 通过；最终 README 与 docs 根级 40 份 Markdown 的 419 个本地目标无断链 |
| Git/数据 | 四份 JSON 仅路径字段改变，目标存在，其他值完全一致；最终 diff、对象完整性及复扫结果见下节 |

执行入口与工作流一致：`uv run --locked --project backend python -B -m unittest discover -s tests -v`、`uv run --locked --project backend python scripts/local/postgres_tests.py`、两份 `tests/http_*_smoke.py`，以及前端 `test`、`test:e2e`、`api:check`、`typecheck`、`lint`、`build`。本地启动通过 manage.py 的新 profile，与现有开发环境隔离。没有为了全绿修改断言、跳过失败或降低安全门槛。

没有后端独立 lint/typecheck 命令，不虚报；Starlette TestClient 的 httpx 弃用提示仍存在。PG 专项、原 API 回归及 SQLite 可能有逻辑重叠，不将通过数量相加成独立覆盖率。本轮未另跑 PG Chromium、历史容量基准或真实模型质量评测；相关历史记录只能作为旧轮次证据。

## 9. 必要修复、本地提交和最终复核

| 本地提交 | 内容 |
|---|---|
| `02509c3` — `docs: redact local paths from evaluation metadata` | 4 份评测 JSON 的 report_path/fixture_path 改为仓库相对路径；只改路径值并补一个末尾换行，评测值、hash、预算和历史结论不变 |
| `047ab9b` — `docs: prepare AGPLv3 candidate and preserve third-party notices` | 完整 LICENSE.candidate、README 候选说明、依赖版本清单、第三方原文告知及审查依据；gitattributes 防止 Git 自动改变原文告知的混合换行字节 |
| 本报告所属普通提交 | 最新审核报告和旧文档/索引入口；未更改产品、迁移或测试标准 |

许可候选提交后已再次读取 HEAD 的 677 个唯一 blob 和本机对象库的 820 个 blob，已知本机凭据精确比对仍为 0 命中，作者身份没有新增私人信息；媒体内容/元数据与初始审阅一致。`git fsck --full --strict` 退出 0，仅提示本机 dangling 对象，没有缺失或损坏对象。

本报告所属 SHA 使用 `git log -3 --oneline` 查询，避免自引用 SHA；最终提交后继续复扫并核对分支计数、工作区和远程引用，结果随本轮交付说明提供。

本轮三个普通提交（包含本报告）完成后的候选范围为 **20 个可达提交，相对本地 origin/main 领先 19、落后 0，其中 17 个不在任一远程跟踪分支中**。提交后收口命令检查工作树为空；被忽略的私有配置、合成环境和测试证据继续保留本机。正式 LICENSE 及历史隐私决定仍未放行，新增提交不改变此结论。

暂存许可证候选时发现 Windows Git 自动转换了原文集合中的 1,132 个 CRLF，导致原文指纹无法逐字节复核。已仅为原文告知设置 `-text`，重新从工作树暂存，确认 index 与工作树 **1,086,634 字节完全相同**；候选和清单固定 LF。没有改写上游许可文本或放宽测试。

空白检查如实记录：本轮自有文件的 `git diff --check` / 暂存检查通过；包含原文告知的全量暂存检查退出 **2**，有 **1,637 条上游原文空白告警**（混合 CRLF、行尾空格、原文件末尾空行），均定位 `THIRD_PARTY_NOTICES.txt`，为保留原文未修改。全分支旧 Markdown/测试还存在此前记录的空白告警，不将全量检查说成全绿；这与功能/安全测试失败不同。

## 10. 未解决的发布阻塞与下一轮顺序

- [ ] 负责人最终选择 AGPLv3 版本表达、确认自有代码/协作贡献/素材授权权利，将候选转为正式 LICENSE 并确定版权主体。
- [ ] 确认 PyMuPDF 上游文件级标识不一致的处理及保留方式；必要时取得 Artifex 答复，不能称为已消除所有法律不确定性。
- [ ] 按最终授权补齐适用界面法律告知和实际对应源码入口。当前只有候选文档，不能声称这项已实施。
- [ ] 明确接受历史占位身份和旧本机用户目录/路径公开，或另行批准 B/C。仅当前文件脱敏尚不足以移除历史披露。
- [ ] 本轮无远程写入授权；真实 Actions 和 GitHub 页面验收尚未完成。

后续仅在相应批准后执行：

1. 确认许可，完成正式许可/告知普通提交；确认历史隐私方案，按批准范围完成处理并全量复扫。
2. `git fetch origin`，核对远程差异、计划公开对象、工作树和提交范围；有新变化先审阅，不默认覆盖。
3. 再取得当前具体分支和 SHA 的普通推送授权。**功能分支一经推送可能立即公开全部可达历史，不能把 PR 创建当作首次公开时点。**
4. 普通推送 `codex/s5-1-postgresql`，不强推、不 mirror/all；检查所推 SHA 的三组真实 Actions，必要修复追加提交并按授权推送。
5. CI/许可/隐私通过后，经授权创建 PR；明确授权后使用保留开发提交的 merge commit 合入 main。
6. 检查 GitHub 默认分支 README、截图、正式 LICENSE/第三方告知、源码和 Actions；完成后才将默认分支仓库地址用于最终简历展示。无需 Release 或公网部署。

## 11. 七个最终问题

1. **是否推荐 AGPLv3？** 推荐，保留现有 PDF 链路，符合公开源码作品集方向；属于附最终授权和告知条件的建议。
2. **许可还需确认什么？** only/or-later、版权与协作权利、PyMuPDF 文件标识差异、第三方原文及适用界面/源码入口；二进制/镜像以后另审。
3. **历史有不希望公开的信息吗？** 有可能：数字型 Windows 用户目录和本机仓库路径；未发现真实个人作者邮箱，是否接受这些信息由负责人决定。
4. **必须改写历史吗？** 没有已确认秘密使其必需；接受 A 可保留原史。如要求删除旧路径，需要另行批准隔离副本改写或独立公开仓库，本轮未执行。
5. **有秘密或个人数据泄漏风险吗？** 未发现已提交真实凭据/真实求职资料；仍有已知历史环境隐私和启发式扫描的剩余风险，本机真实配置必须继续排除。
6. **作品集达到展示标准吗？** 内容、真实截图、工程说明和本地复现达到技术展示标准；许可/历史公开决定尚未完成，不能称为已可公开发布。
7. **具备申请最终推送授权的条件吗？** **尚不具备无条件推送的条件。** 可提交本报告供负责人审阅并先决定许可与隐私；完成上述实质门槛后再申请具体推送授权。

本轮在本地提交和复核后停止，等待负责人审核。此报告不构成任何远程操作授权。
