# OfferPilot LICENSE 正式化与 GitHub 发布准备

日期：2026-10-18（Asia/Shanghai）。范围：应用源码 Git 仓库、本地作品展示和无 Key 演示；不分发依赖安装目录、二进制、镜像或公网服务。

**B. 仍有发布阻塞，暂不建议推送。**

负责人已明确选择 **AGPL-3.0-only**、保留 PyMuPDF、接受普通历史开发路径并保留完整 Git 历史。这些决定无需再次批准。候选许可证全文、依赖相容方向、安全复扫和本地演示检查均有支持证据；**原创内容及 AI 协作成果的授权权利来源仍缺少可核实确认**，不能把 Git 占位身份推断成版权归属。因此保留 `LICENSE.candidate`，未建立正式 `LICENSE`，未提前声明项目授权已经生效。本轮已提出具体来源确认问题，尚无回复作为依据。

## 1. 实际 Git 状态与本地提交

| 项目 | 本轮开始时重新核查的结果 |
|---|---|
| 分支 | `codex/s5-1-postgresql`，沿用原分支，无 upstream |
| 初始 HEAD | `6fe20acafabff838f1831e695843bc5c68d3fa37` |
| 最新三次提交（从新到旧） | `6fe20ac` → `047ab9b` → `02509c3`；此前提供的三个 SHA 确实存在 |
| 初始工作树 | 干净 |
| HEAD 可达提交 | 20 个 |
| 相对本地 `origin/main` | 领先 19、落后 0；17 个提交不在任一远程跟踪分支 |
| 本地 main / origin/main | `ca9db6f8fc4cf43aa50e049b075493c2f7ac7769` |
| 远程配置 | `https://github.com/jayoffans/OfferPilot.git` |

本轮没有 fetch、更新远程跟踪引用或查询新远程 heads；以上差异只针对已有本地 refs，不是 2026-10-18 远程实时状态保证。没有 push、PR、远程 merge、Release、仓库设置修改、外部代码上传或历史改写。

本报告、README 状态同步、PyMuPDF 文件头告知补充及旧报告入口统一追加为一个普通本地提交，提交主题为 `docs: audit AGPL licensing readiness and preserve upstream notices`。其 SHA 用 `git log -1 --format='%h %s'` 获取，避免在同一提交内写入自引用 SHA。追加后 HEAD 共 21 个提交，相对未变的本地 `origin/main` 领先 20、落后 0，其中 18 个不在任一远程跟踪分支；交付时再核对工作树为空。原有 20 个提交及作者信息全部保留。

## 2. LICENSE 文本、SPDX 与生效状态

本轮程序读取完整候选并检查：

- 34,520 字节，SHA-256 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`；无 BOM，LF 换行。
- 与 [PyMuPDF 1.28.2 官方完整 COPYING](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING)逐字节一致；第 0～17 条、条款结束及附录完整。
- 与 [SPDX license-list-data v3.27.0 的 AGPL-3.0-only 文本](https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/AGPL-3.0-only.txt)规范化空白后完全一致，原始文件因排版空白不同不宣称字节一致。
- [SPDX 官方说明](https://spdx.org/licenses/AGPL-3.0-only.html)区分项目的仅版本 3 选择与标准附录的 “or later” 示例；全文保留示例是标准文本要求，不能为了 only 改写许可正文。项目没有追加 or-later 授权。
- 文本中的 FSF 2007 版权属于许可文档本身，不代表 OfferPilot 代码属于 FSF。没有填入猜测的人名、组织或版权转让声明。

[GNU 正式入口](https://www.gnu.org/licenses/agpl-3.0.html)本轮请求超时；不把它记为成功下载，完整文本比对依据是上述固定版本官方副本与 SPDX。

**正式授权未生效。** [LICENSE.candidate](../LICENSE.candidate)仍是候选，没有 `LICENSE`，Python/npm 项目元数据也没有提前声称 AGPL 授权。GitHub 正常识别所需的根目录正式 LICENSE 尚未启用，因此该展示项目仍是待完成项；标准文本匹配通过不能冒充 GitHub 远程识别已验证。

## 3. 计划授权范围与版权/贡献权利

AGPL-3.0-only 已经是负责人选定的版本。正式化时拟覆盖其**有权授权的 OfferPilot 原创代码、配套文档、测试与合成演示素材**，无需声称负责人独占全部版权；第三方代码、模板、库、图标及原文声明继续按各自许可。授权不能自动覆盖用户上传的简历、个人数据、第三方商标或未来加入的素材，也不能追溯宣称旧历史早已获得本项目许可。

本轮检查 Git 作者/提交者、初始提交及后续引入记录、自有代码中的来源/版权标记、生成入口和面试贡献说明：20 个提交只有 19 个 `Codex <codex@localhost>` 与 1 个本地检查点占位身份。未发现自有代码中明确标注的未经许可复制来源；但“没有标记”和“均由 AI 提交”都不能证明不存在复制、协作者或雇主权利，也不能证明 AI 输出必然可由负责人再许可。仓库没有可核实的负责人来源确认、贡献者许可或其他权利链材料。**本报告不声称已经发现侵权，而是按本次任务的权利确认要求暂缓授权结论。**

暂缓原创内容授权结论的文件范围（相对于仓库根目录）：

| 范围 | 现有证据及需补充的确认 |
|---|---|
| `backend/src/offerpilot_api/` 全部实现与 Prompt，`backend/evaluations/` 合成工厂/样本/报告，`backend/migrations/versions/` 与迁移装配 | 分阶段提交与验收可追踪开发；需确认项目内创作/AI 协作来源，以及不存在未授权的外来代码或雇主材料 |
| `frontend/src/`（下列生成文件单列）、`frontend/e2e/`、`frontend/scripts/` 与项目配置 | 同上；依赖导入不等于复制其实现，不能把依赖版权转为项目版权 |
| `tests/`、`scripts/`、`.github/`、根目录项目说明/配置及 `docs/` 的原创内容 | 可核对本机创建和验证记录；仍需确认有权公开相应文档、测试和素材 |
| `docs/images/demo/*.png` 11 张与 `backend/evaluations/fixtures/normal_chinese.synthetic.pdf` | 生成脚本、合成数据和哈希一致，未发现真实私人材料；真实性/隐私验证不能单独替代创作授权确认 |

已识别的工具来源单列，不能归为“负责人全部原创”：

| 文件 | 处理边界 |
|---|---|
| `frontend/src/lib/schema.d.ts`、`frontend/openapi.json` | 来自本项目实际 OpenAPI 及 openapi-typescript 7.13.0/导出脚本；保留生成标记与重生成入口，不推断生成器全部代码被复制进输出 |
| `frontend/next-env.d.ts`、`frontend/AGENTS.md` | Next.js 生成的类型引用/开发规则，保留来源，不另造原创版权声明；Next 原有 MIT 见第三方告知 |
| `backend/migrations/script.py.mako` | Alembic 模板形态，按第三方模板来源处理，保留 Alembic MIT 告知，不声称独占或移除其适用条款 |
| `LICENSE.candidate`、`docs/THIRD_PARTY_NOTICES.txt`、`docs/PYMUPDF-1.28.2-NOTICES.txt` | 上游许可或版权原文，按原条件保留，不作为 OfferPilot 原创内容重新授权 |

缺少的是来源和授权能力的事实确认，**不要求虚构真实姓名、独占权、版权转让或不存在的签名**。负责人可确认除已列第三方/生成内容外均为其组织创作且有权授权，或列出例外路径/来源逐项核查；在得到事实依据前，不扩大授权范围。

## 4. PyMuPDF / MuPDF 与第三方依赖

正式 `services/resume.py` 在 API 进程内直接使用 PyMuPDF 校验和提取文本；证据定位依赖其输出，测试/合成工厂亦调用它。保留锁定及安装的 **PyMuPDF 1.28.2 / MuPDF 1.28.2**，不以动态导入、未改库、免费使用或作品集定位免除组合许可审查。

[官方许可文档](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)、[Artifex 说明](https://artifex.com/licensing)、固定 tag 全文和本机包内材料交叉核对：包级 METADATA 与 64 字节 COPYING 摘要提供 AGPL3/商业双许可描述；`__init__.py` 是 GPL-3.0-only 文件头；表格模块是 AGPLv3-or-later，其中 `table.py` 保留 Jeremy Singer-Vine 的 MIT 派生代码声明；`utils.py` / `__main__.py` 保留 Harald Lieder 的版权与 AGPL3 告知。不能把这些全部统一改标为本项目 only。

依据 GPLv3/AGPLv3 第 13 条的组合许可及各自条款，本轮未发现阻止本源码仓库采用 AGPL-3.0-only 的明确依赖冲突；这不是上游已统一其标识或本项目获得商业授权的声明。本轮补入 [PyMuPDF 文件头原文](PYMUPDF-1.28.2-NOTICES.txt)：8 个文件头与已装包及固定官方 tag 逐字节一致，保留完整 MIT 段、GPL/AGPL 条款与原作者；原文文件固定 Git 字节保存属性。

全量复核结果：55 个 Python、636 个 npm 锁条目与[清单](DEPENDENCY-LICENSE-INVENTORY.json)精确一致；54 个 Python、512 个 npm 已装版本无漂移，695 个已有许可文件指纹引用核对通过，384 个不同原文哈希均在[既有告知](THIRD_PARTY_NOTICES.txt)中。原清单/告知的字节未改。25 个已装 npm 包没有独立许可文件，124 个可选平台包未安装，故只能声称锁元数据与已获取原文核对，不能声称所有平台制品逐文件审计完成。

MIT/ISC/BSD/Apache、LGPL、MPL 及 CC-BY 数据条款均按原表达式保留；OR 与 AND 不混用。Lucide ISC 与 Feather MIT 已保留。UI 使用系统字体名，未跟踪字体程序；合成 PDF 的 CJK 字体未嵌入。没有发现外部照片/插画或简历模板。详细相容路径、原生组件、NOTICE 和未来二进制边界见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.md)。

## 5. 网络交互与界面告知方案

AGPL 第 0/5(d) 条界面告知与第 13 条适用修改版本的远程交互义务分别核查；不能因“没有修改 PyMuPDF 本体”就认定集成应用自动免责。当前只有 loopback 本地演示，无公网服务，且项目许可未正式生效，**本轮未向 UI 添加尚未生效的许可声明**，也没有声称已履行未来服务义务。

权利确认并正式化后，最小实现位置是现有 `workspace-shell.tsx` 页脚及未登录 `auth-screen.tsx` 页脚：提供“源码”“AGPL-3.0-only”“第三方声明”，连同适当版权/无担保及按条款再分发说明，沿用响应式布局。代码级许可告知修改后执行前端完整检查及 Chromium 回归，不增加新业务页面或设置。

普通 GitHub 首页链接不足以证明用户能获取**实际运行版本**的完整对应源码。当前远程 main 还是此前初始化版本，本轮未推送完整应用；授权发布后应核对具体发布 SHA/分支、源码完整性、依赖对应源码获取安排及运行/构建脚本，在提供适用网络服务或再分发制品前验证入口可用。源码义务不要求公开 Key、个人数据或私有运行库。

## 6. Git 历史与敏感信息

保留完整历史，不清洗普通路径、不改作者、不 rebase/squash/amend、不强推。历史数字型 Windows 目录和普通本机路径已获负责人接受，不列为阻塞。占位作者身份保留，不猜 GitHub noreply 邮箱。

初始快照复扫覆盖全部本地 refs 的 20 个提交、682 个历史 blob，另 9 个非 commit tree refs 独有的 25 个 blob，合计 707 个唯一 blob，以及 355 个可跟踪工作树文件。候选按文件版本/类别为连接串 14、字面凭据 62、普通用户目录 13、手机号形态 13、邮箱 2；重复历史和工作树会重复计数，**不是泄漏数量**。人工核对为合成/示例/环境插值/拒绝凭据 URL 的测试、普通路径或第三方公开署名；未发现已确认真实 Key、Token、私钥、真实数据库口令或个人资料误提交。

`backend/.env` 的 Key 和 `.env.s5-postgres` 的三类密码均被忽略；四个值及 URL 编码形式只在本机内存与全部 825 个 Git blob、提交元数据及可跟踪文件精确比对，0 命中，没有输出值、摘要或片段，也未请求供应商验证有效性。Git 可达历史没有运行 DB、真实 `.env`、ZIP、备份、日志或私钥。最终新增文档/告知在暂存与提交后再次复扫，不以旧计数代替新增内容检查。

本轮新增内容的提交前复扫覆盖 357 个可跟踪文件，四个已知本机秘密与当时全部 837 个 Git blob 及工作树仍为 0 命中。新增报告规则命中 0；新增告知仅两处上游公开版权邮箱（第 286、303 行），与锁定包原文一致，应当保留，不属于负责人私人资料。规则中的邮箱文件/类别记录从 2 变为 3，其他类别计数未变。

`git fsck --full --strict` 初始检查退出 0，只报告本地 dangling 对象，没有缺失或损坏。指定分支的普通推送将公开全部可达历史；非 commit checkpoint、其他 refs、reflog 与不可达对象不应通过 `--all`、`--mirror` 或整个 `.git` 归档顺带发布。

规则扫描不是完整 DLP，不保证识别任意姓名、混淆秘密、压缩/加密内容；未审远程独有对象、外部备份或其他 clone。全部本机对象的补充检查是已知秘密精确比对，不冒充所有对象的一般规则全审。

## 7. README、演示、链接与贡献表述

11 张 PNG 存在，总计 1,721,904 字节，manifest 的 hash/尺寸一致，PNG 只有 IHDR/IDAT/IEND；逐张可视复核显示合成学校/公司与测试邮箱，密码/Key 框为空，候选持续标 Mock。唯一历史 PDF 为登记合成夹具，2,081 字节/2 页，与工厂字节相同，0 链接/附件，无真实简历。没有替换或重生成公开截图。

README、DEMO、INTERVIEW、PLAN、LOCAL_SETUP、当前架构导读和实际入口一致；完整 MVP、PG、导出/注销均保留，没有新增业务能力。基础演示不需要 Key，Mock 与唯一一次历史真实模型样本分开，未虚构总体模型准确率、用户规模或生产部署。面试稿明确要求填写本人真实职责，AI 协作不包装成独立完成；权利确认也不等于个人面试职责已经确认。

初始 README 与 docs 共 40 份 Markdown、419 个相对文件目标通过检查；新增报告及入口同步后，共 41 份 Markdown、437 个相对文件目标通过，缺失 0、本机绝对路径链接 0。LICENSE 的 GitHub 识别因未正式化仍待完成，不用本地全文匹配冒充远程展示验收。

## 8. 本轮实际验证记录

仅使用隔离合成环境和免费 Mock，无真实模型请求。复用已锁定的本机 Python 3.11.5、uv 0.12.19、Node 22.16.0 / npm 10.9.2、Next 16.4.0；未声称重新完成全新跨平台依赖安装。

| 本轮实际执行 | 结果 |
|---|---|
| LICENSE 全文/标识 | 固定 PyMuPDF 副本字节一致，SPDX 规范化空白一致，第 0～17 条/附录完整；正式化待权利确认 |
| 第三方检查 | 锁文件/清单、已装版本、695 个许可文件指纹引用与新增 8 个原文文件头通过 |
| 截图 | `uv run --locked --project backend python scripts/collect_demo.py --check`：11 张合成截图通过 |
| README/docs 相对链接 | 最终 41 份 Markdown、437 个目标通过，缺失/本机绝对路径链接均为 0 |
| Git/敏感信息 | refs/工作树规则扫描、已知凭据内存精确比对及 `fsck` 如第 6 节；最终 diff/暂存与提交后复扫收口 |
| 全新 SQLite profile | `manage.py --profile release-license-20261018 init --database sqlite` → `seed` → `status` 全部退出 0，迁移至 0010；Mock 候选保持 pending，付费调用 0 |
| 真实 API/Web 启动 | 同 profile 的 `api` / `web` 启动成功；API `/health` 200，Web `/login` 200 |
| 同源 HTTP 流程 | 真实 Next 代理登录 200、`/me` 200、工作台三条合成申请、候选保持 pending、带 CSRF 退出 204、退出后 `/me` 401；自建两个服务已停止，3000/8000 已释放 |
| diff/字节检查 | 完整暂存 `git diff --cached --check` 退出 0；新增告知 8 个片段与包内原字节一致，增加集合结束标记后无末尾空行告警；既有候选/锁/清单/原文集合字节未改 |

本轮只改文档、原文告知和 Git 字节保存属性，没有前后端业务代码、依赖、数据库或 CI 配置变更，**没有重跑**完整 SQLite/PG/前端/Chromium/OpenAPI/ESLint/构建/类型检查。以下仅为[上一轮实际日志](GITHUB-FINAL-RELEASE-REVIEW.md)支持的历史基线，不能计为本轮新通过：SQLite 598 通过/53 PG 专项跳过；PG 专项 53 与原回归 288 通过；前端 140、Chromium 36 通过；OpenAPI、类型、ESLint、构建通过。Starlette TestClient 现有 httpx 弃用提示保留，未升级依赖。

启动探测初稿误用了不存在的 `/auth/session` 并收到 404；核对真实契约后改用 `/me`，重新完成上述全流程。没有改应用白名单或为通过检查新增接口。

本机检查摘要位于忽略目录 `frontend/artifacts/release-ready/`，仅本机存在，不作为公开源码依赖；私有演示凭据和数据保留在忽略的 `.local/`。报告不附凭据或运行数据库。

**远程 GitHub Actions：尚未执行，等待最终推送授权。** 本地检查不能代替托管 runner、远程 LICENSE 识别或 GitHub 页面验收。

## 9. 剩余阻塞与下一轮安全步骤（仅准备）

实质阻塞是第 3 节原创内容/协作成果的授权权利来源尚无确认，因此正式 LICENSE 和适用 UI 告知未启用。未发现需要换掉 PyMuPDF 的明确许可冲突、确认的秘密泄漏或必须清洗普通历史路径的理由。不得为获得 A 状态假定上述权利已经确认。

1. 取得第 3 节来源事实确认，例外文件逐项处理；无需重新批准已选定的 only 版本或普通路径公开决定。
2. 无实质冲突后，将未经改写的候选移为根目录 `LICENSE`，删除候选，明确原创授权范围、第三方例外及 SPDX `AGPL-3.0-only`，同步 README/元数据和适用界面告知；代码/配置变化按实际范围完整回归，再次检查全部 diff 和秘密，追加普通本地提交。
3. **再次明确授权后**才进入远程阶段：fetch 并确认远程状态、拟发布分支/SHA/可达历史及最新差异；若远程变化先审查，用保留历史的普通合并处理，禁止历史重写或强推。
4. 单独获准普通推送功能分支后才 push；公开仓库功能分支推送可能立即公开全部可达历史，不能把 PR 当成首次公开时点。禁止 `--force` / `--all` / `--mirror`，不直接推 main。
5. 观察所推 SHA 的真实 GitHub Actions；失败如实修复、追加提交并按授权普通推送。许可/CI通过后才按批准创建 PR 并审查。
6. 经再次明确授权后，以保留历史的 merge commit 合入 main；检查默认分支 README、11 图、DEMO、正式 LICENSE、第三方告知、实际源码入口及 Actions。当前没有创建 Release 或部署的任务。

**最终状态：B. 仍有发布阻塞，暂不建议推送。** 本轮完成本地审查、必要告知修复、验证与普通提交后停止；没有任何远程写入授权。
