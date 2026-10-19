# OfferPilot LICENSE 正式化与 GitHub 发布准备

更新：2026-10-19（Asia/Shanghai）。本报告接续 2026-10-18 的许可与历史审查；范围仍为源码 Git 仓库、本地作品展示和无 Key 演示，不分发依赖安装目录、二进制或镜像，不部署公网服务。

**A. 本地发布准备完成，等待用户授权推送。**

## 1. 当前分支与提交状态

本次续作开始时工作树干净，分支为 `codex/s5-1-postgresql`，HEAD 为 `71cf2a6e1161728d632872109a729744a4252f44`，共 21 个可达提交，相对本地 `origin/main` 领先 20、落后 0；18 个提交不在任一远程跟踪分支。main / origin/main 均为 `ca9db6f8fc4cf43aa50e049b075493c2f7ac7769`，没有 upstream。

没有 fetch 或更新远程跟踪引用，差异仅针对已有本地 refs，不保证远程当前未变化。远程配置仍为 `https://github.com/jayoffans/OfferPilot.git`。本轮没有 push、PR、远程 merge、Release、仓库设置修改、外部代码上传或历史重写。

## 2. LICENSE 正式生效与完整性

负责人已回复“确认有权授权，且没有上述例外”，解除此前 B 状态的来源事实阻塞；已将 `LICENSE.candidate` 原样转为根目录正式 [LICENSE](../LICENSE)，移除候选文件，不保留两份模糊的项目授权。[COPYRIGHT](../COPYRIGHT.md) 和 README 明确仅 **AGPL-3.0-only** 的授权选择及范围。前端本地 `/LICENSE.txt` 与根 LICENSE 全文字节相同，未往标准许可正文添加项目声明。

完整文本为 34,520 字节，SHA-256 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`；无 BOM、LF，第 0～17 条、条款结束及附录完整。此前与 [PyMuPDF 1.28.2 官方完整 COPYING](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING) 逐字节一致，与 [SPDX 固定版本文本](https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/AGPL-3.0-only.txt) 规范化空白后一致，本次转正重新核对原字节。

[SPDX 官方说明](https://spdx.org/licenses/AGPL-3.0-only.html)区分 only 与 or-later；标准附录末尾的 later 使用示例不构成项目的额外授权，不为 only 修改全文。FSF 2007 版权属于许可文档，不代表 FSF 拥有 OfferPilot 代码。[GNU 正式入口](https://www.gnu.org/licenses/agpl-3.0.html)抓取超时的旧记录如实保留，未冒充成功下载。

根 LICENSE 标准文件名和全文匹配满足本地识别条件；GitHub 实际识别与页面显示仍须在授权推送后验收，未标为远程已通过。

## 3. AGPL-3.0-only 的准确范围

授权覆盖已确认有权授权的 OfferPilot 原创代码、配套文档、测试、Prompt 与合成演示素材。使用、修改和再分发遵守完整条款，包括适用的声明、无担保、对应源码和网络交互义务；本地作品展示/近期不商业化不是禁止商业使用的附加许可。

第三方库、模板、图标、数据及原文告知继续按各自许可，不被 OfferPilot 统一重新授权。用户上传简历、真实个人数据、秘密、第三方商标和未来另行加入素材不自动获得本项目许可。未宣称旧提交在当时已经载有本次授权声明。

## 4. PyMuPDF 与第三方许可核查

保留锁定及安装的 **PyMuPDF 1.28.2 / MuPDF 1.28.2**。正式 API 进程直接用其校验/提取 PDF，合成工厂和测试也使用它；不以动态导入、未改库或作品集定位排除组合义务。

[官方文档](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)、[Artifex](https://artifex.com/licensing)、固定 tag 与包内资料交叉核对：METADATA/64 字节 COPYING 是 AGPL3/商业双许可描述，不是完整全文；`__init__.py` 有 GPL-3.0-only；表格模块有 AGPLv3-or-later，`table.py` 保留 Jeremy Singer-Vine 的 MIT 派生代码声明，`utils.py` / `__main__.py` 保留 Harald Lieder 的版权及 AGPL3 告知。上游差异按原条件保留，不能改标为项目 only。

依据各自条款及 GPLv3/AGPLv3 第 13 条组合路径，未发现阻止本源码方案的明确依赖冲突；不声称上游已统一标识或本项目取得商业授权。[8 个文件头原文](PYMUPDF-1.28.2-NOTICES.txt)与固定 tag/安装包相同，MIT、GPL/AGPL 及原作者均保留。

2026-10-18 完整审查覆盖 55 个 Python、636 个 npm 锁条目，54/512 个本机已安装版本、695 个许可文件指纹引用及 384 个不同原文。2026-10-19 仅续核声明、版本及指纹一致性；Python 锁未改，npm 锁只增加根项目 license，636 个非根条目不变。清单同步项目许可状态、npm 锁指纹和 PyMuPDF 原文位置说明；上游原文块未改，仅告知汇编前言同步正式状态。

未安装的 uvloop/124 个 npm 平台可选包以及 25 个已装 npm 包无独立许可文件的边界继续保留。LGPL/MPL/CC-BY/Apache 等各自义务、OR/AND 和原生组件不能抹除。源码仓库不附 .venv、node_modules、Chromium、wheel、镜像或预构建程序，未来制品须按实际内容另审。依据见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.md)、[清单](DEPENDENCY-LICENSE-INVENTORY.json)和[原文](THIRD_PARTY_NOTICES.txt)。

## 5. 版权、贡献与 AI 协作确认

负责人明确确认：除已列第三方依赖、图标及工具生成内容外，代码、文档与合成截图均由其在本项目组织创作且有权按 AGPL-3.0-only 公开，没有未授权的雇主、学校或第三方材料例外。此前暂缓的 `backend/src`、评测/迁移、`frontend/src/e2e/scripts`、`tests`、`scripts`、项目说明/配置及原创演示素材已据此解除来源阻塞，不要求独占版权、真实姓名或虚构的转让材料。

Git 既有 Codex/本地检查点身份仅是开发记录，不作为权利归属证明。COPYRIGHT 和 UI 的“OfferPilot 各贡献者”是各权利人集合表述，不是新设组织或版权转让。未发现自有实现中明确标注的未经授权复制来源；检查不是任意代码来源的绝对证明，今后引入材料仍须保留授权证据。

生成内容继续单列：OpenAPI JSON、openapi-typescript 类型保留生成标记/入口；Next.js 的 `next-env.d.ts` 与开发规则不声称全部原创；Alembic 模板和 Lucide/Feather 保留适用第三方条款。11 张截图与白名单 PDF 来自合成工厂，无第三方照片/插画或字体程序；PDF CJK 字体未嵌入。个人面试职责仍须据实填写，来源确认不代表本人独立完成所有设计实现。

## 6. 网络交互源码告知

现有工作台及登录/注册页脚复用 `LegalNotice`，显示版权、AGPL-3.0-only、无担保、依条款修改/再分发，以及“项目源码”“许可全文”“第三方声明”。许可全文从本地固定静态文件获取，无须登录，字节与根 LICENSE 一致。链接均新标签打开，保留原页未保存输入；正常文档流及换行支持移动布局，没有新业务页面/设置。

项目源码/第三方声明指向已知 GitHub 仓库的 `codex/s5-1-postgresql` 分支。该完整分支本轮未推送，远程入口的可用性不能提前说成已验证。当前仅 loopback 本地演示，无公网服务；普通分支入口本身不能证明未来用户能获取**实际运行版本**的完整对应源码。

授权发布后须核对具体发布 SHA/分支、构建/运行资料及适用依赖的对应源码获取安排。任何适用网络服务/制品分发前再核实第 0/5(d)/6/13 条和第三方义务；不公开 Key、个人数据或私有运行库。当前无需新增自动打包端点。

## 7. Git 历史保留

普通数字型 Windows 用户目录和开发路径已获准公开，不再列为阻塞。完整历史、作者和原提交保留，没有 rebase、squash、amend、历史清洗或强推。新增许可采用普通追加提交。指定功能分支推送将公开全部可达历史，禁止用 --all、--mirror 或整个 .git 归档发布其他 refs/本机对象。

## 8. 敏感信息检查

此前审查全 refs、工作树及本机已知凭据的精确比对未发现确认的误提交秘密。71cf2a6 收口扫描为 21 个提交、719 个唯一 blob、357 个可跟踪文件；四份既有秘密加本机随机演示口令与全部 841 个本机 blob、提交元数据和可跟踪文件比对为 0 命中。规则候选是测试/占位/插值、普通路径和上游公开署名，不作为泄漏数量。

本次正式化提交前再次完成 refs/工作树复扫：21 个提交、719 个唯一 blob、360 个可跟踪文件；候选仍为连接串 14、字面凭据 62、普通路径 13、手机号形态 13、上游邮箱 4，没有新增未解释候选或确认的秘密泄漏。六个已知本机秘密（原四项及两套隔离演示随机密码）与当时全部 862 个本机 blob、360 个工作树文件、提交元数据和两类 diff 精确比对，0 命中；本轮新增媒体仍为 0。提交后再以同一入口复扫，结果留本机日志。

真实 .env、数据库、导出 ZIP、备份、日志、私钥和演示凭据保持忽略；上游版权作者公开邮箱按原文保留。仅输出脱敏位置和计数，不输出值、摘要或片段，不请求供应商验证 Key。

扫描有启发式局限，不保证识别所有姓名、混淆秘密或加密内容；未审远程独有对象、外部备份及其他 clone。全部本机 blob 的补充检查只证明已知秘密没有精确命中，不冒充全对象完整 DLP。

## 9. README、11 图和作品展示

README 明确正式许可、COPYRIGHT、第三方范围和使用/再分发义务；全仓 51 份 Markdown、460 个相对文件目标收口检查通过，缺失、候选文件断链和本机绝对路径链接均为 0。11 张公开 PNG 保留 2026-10-18 已审阅合成业务快照（共 1,721,904 字节），不因本轮测试自动覆盖，manifest 检查再次通过；新增页脚另以本机截图复核。图中密码/Key 框为空、Mock 持续标识，原 PNG 只有 IHDR/IDAT/IEND。唯一历史 PDF 为登记合成样本，2,081 字节、2 页，无链接/附件，与工厂一致。

DEMO、当前架构、LOCAL_SETUP 和 INTERVIEW 的功能/命令边界未改；没有新业务能力、真实用户规模或生产部署声明。无 Key 手工闭环成立，Mock 与一次历史真实合成评测分开，不推断整体模型准确率。

## 10. 本轮验证记录（2026-10-19）

本机 Windows、Python 3.11.5、uv 0.12.19、Node 22.16.0、npm 10.9.2、Next 16.4.0、PostgreSQL 18.6；只用隔离合成资料及免费 Provider 夹具，无收费模型请求。依赖无新增或升级。

| 检查 | 本轮实际结果 |
|---|---|
| LICENSE / 元数据 / 第三方 | 两份标准全文与原候选哈希一致；JSON/TOML、npm 根许可、安装后的后端 License、库存锁指纹、未变的 55/636 第三方版本/许可及 8 个 PyMuPDF 头通过；原始上游告知块字节一致，仅自写前言同步 |
| uv 环境 / 锁 | `uv sync --locked --offline` 与 `uv lock --check --offline` 退出 0，editable 许可元数据重建成功 |
| SQLite 完整回归 | 651 项运行，598 通过、53 项 PG opt-in 跳过，0 failure/error；718.513 秒，退出 0 |
| PostgreSQL 专项 / 原 API 回归 | 专项 53/53 通过（94.542 秒），原 API/领域 288/288 通过（718.366 秒），无失败/跳过，runner 退出 0；不将重叠逻辑相加成独立覆盖率 |
| 前端 Vitest | 13 文件、140 项通过 |
| OpenAPI / ESLint / 构建 | 三项退出 0，构建成功，无部署 |
| TypeScript | `tsc --noEmit` 退出 0 |
| Chromium 完整 E2E | 36/36 通过，零重试，5.6 分钟，退出 0；SQLite 隔离真实 API/Web，免费模型夹具 |
| 本地启动 / 页脚 / 移动端 / 未保存输入 | 全新 `license-final-20261019` SQLite 环境 init→seed→status 退出 0；真实 API health 200、浏览器登录/三申请、8 个布局无横向溢出或表单重叠、本地全文字节相同、新标签保留未保存输入、CSRF 退出 204；付费调用 0；自建及 E2E 进程已停止，四端口已释放 |
| 截图 / 链接 / Git diff / 秘密扫描 | 11 张公开图 manifest、51 份 Markdown/460 个目标、完整暂存 `diff --check` 退出 0；规则复扫与已知秘密精确比对无新增泄漏证据 |

PostgreSQL 首次尝试因 Docker daemon 未运行退出 1，未执行数据库测试；已通过 CLI 启动 Docker，重跑结果单独记录，不把首次失败记为通过。Python 为保持现有 setuptools>=70 使用 PEP 621 `license={text=...}` 兼容格式，不声称生成 PEP 639 License-Expression，不为元数据升级构建工具。Starlette TestClient 弃用提示仍存在。

页脚专项探针首轮把登录区域内的 footer 当作全局 contentinfo 定位而超时；按实际 HTML 语义修正探针后，8 个桌面/390px/短窗口场景及输入保留检查通过，没有改应用或放宽断言。截图已可视核对，密码/Key 为空或登录后已清除，只含合成资料。

日志和本机截图在忽略目录 `frontend/artifacts/license-final-20261019/`，私有演示资料在忽略的 `.local/`；它们不纳入公开提交。2026-10-18 的启动及其他历史基线不计为本轮新通过。未重跑历史容量基准、PG Chromium 或完整备份恢复演练。

**远程 GitHub Actions：尚未执行，等待最终推送授权。**

## 11. 本地新增提交

保留 `71cf2a6` 审查提交，追加一个 `chore: formalize AGPL-3.0-only licensing and source notices` 普通本地提交，包含正式 LICENSE/COPYRIGHT、兼容许可元数据、页脚/全文入口、清单/文档同步及本报告。具体 SHA 用 `git log -1 --format='%h %s'` 查询，避免自引用 SHA；不 amend 原提交。追加后 HEAD 共 22 个可达提交，相对未变的本地 `origin/main` 领先 21、落后 0；19 个提交不在任一远程跟踪分支。最终核对工作树干净，原有全部提交/作者保留。

## 12. 发布阻塞与本地状态

版权来源事实阻塞已由负责人明确确认解除；未发现必须换掉 PyMuPDF 的明确依赖冲突、确认的秘密泄漏或其他未解决的重大本地发布阻塞，普通历史路径已获准保留。本轮关键本地检查及相应完整回归通过，故选择 A。远程 CI、LICENSE 识别和页面/分支源码入口是获准推送后的待办，不冒充本地完成即远程已发布。未来二进制/服务化仍按实际制品和版本单独审查。

## 13. 下一轮远程发布安全步骤（仅准备）

1. 用户再次明确批准远程阶段后，fetch 并核对远程状态、拟公开分支/SHA/全部可达历史及最新差异；若有变化先审查，采用保留历史的普通合并，不重写或强推。
2. 单独获准普通推送当前功能分支后才 push；公开仓库功能分支推送可能立即公开全部可达历史，不以 PR 当成首次公开时点。不直接推 main，不使用 --force/--all/--mirror。
3. 查看所推 SHA 的真实 GitHub Actions；失败如实复现修复，追加提交并按授权普通推送。核对 LICENSE/源码/第三方链接在该分支实际可用。
4. 许可/CI 通过后按授权创建 PR 并审查；经再次明确授权才以保留原提交的 merge commit 合入 main。
5. 检查默认分支 README、11 图、DEMO、LICENSE/COPYRIGHT/第三方告知、实际版本源码及 Actions。没有创建 Release 或部署任务。

**最终状态：A. 本地发布准备完成，等待用户授权推送。** 本轮到本地验证和普通提交为止，禁止任何远程写入。
