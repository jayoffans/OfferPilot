# 第三方依赖与许可审查

审查日期：2026-10-18（Asia/Shanghai）。依据为 `backend/uv.lock`、`frontend/package-lock.json`、本机已安装包的许可证元数据和下列上游官方资料。本文记录依赖与发布决策，**不替 OfferPilot 选择项目许可证，也不替代第三方许可证全文**。

## 项目自身的许可待确认

仓库当前没有顶层 `LICENSE`，后端项目元数据也没有声明项目许可证。公开 GitHub 源码与给予开源再分发授权是不同事项；发布前由代码权利人确认项目许可、贡献归属和第三方相容性。此轮没有擅自添加 MIT、Apache 或 AGPL 项目许可。

## PyMuPDF 是发布前的主要许可决策

`backend/pyproject.toml` 声明 `PyMuPDF>=1.27,<1.29`，`backend/uv.lock` 实际锁定 **PyMuPDF 1.28.2**。本机同版本安装包的 `COPYING` / `METADATA` 声明 GNU Affero GPL 3.0 / Artifex 商业双许可；[1.28.2 官方文档的许可节](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)与[该版本上游 COPYING 全文](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING)交叉确认。以下官方资料访问日期均为 **2026-10-18**。

### 实际依赖范围

- **正式运行：** `backend/src/offerpilot_api/services/resume.py` 直接导入 `pymupdf`，用于文件真实性、加密、页数检查及 `page.get_text("text", sort=True)` 文本提取，并映射安全解析错误。不是可选插件，也不仅用于测试。
- **证据契约：** 当前保存的是完整提取字符串、页/段字符区间及哈希；`resume_ingestion/evidence.py` 据此定位引用，未使用 PDF 图形边界框。换解析器可能改变中文顺序、换行、偏移及资源版本，不能承诺同一 PDF 产出相同证据。
- **演示和验证：** `backend/evaluations/pdf_evaluation.py` 同时生成及读回合成中文 PDF；`scripts/local/demo_seed.py`、`scripts/postgres/quality.py`、`scripts/postgres/lifecycle_restore.py`、`frontend/scripts/e2e-backend.py` 及多组上传/隔离/生命周期测试也直接使用 PyMuPDF。只替换正式解析入口仍会保留开发与演示依赖。
- **交付方式：** 当前 Git 仓库交付应用源码与锁文件，不包含 PyMuPDF 源码、wheel、`.venv` 或预构建镜像。没有修改上游库；但“未修改库”“Python 动态导入”“由用户安装依赖”不足以直接推断组合程序免除许可义务。

### A / B / C 方案比较

| 方案 | 合规风险与负责人需要核实的范围 | 修改成本 | 当前作品集适用性 | 后续开发影响 |
|---|---|---|---|---|
| **A. 保留 PyMuPDF，按 AGPLv3 相容方式公开** | 必须确认有权给自有代码授予所选许可、组合程序边界及全部依赖/素材义务；仅写一个 AGPL 标签不能完成验收 | **低于替换方案**：保留已验收解析行为；补齐 LICENSE、版权/告知、源码与构建运行信息；根据适用条款补界面告知/源码入口 | **推荐优先确认**：当前目标本来就是公开工程源码与本地复现，无需为作品集购买闭源授权 | 后续贡献及发布继续遵循所选 copyleft 条款；若以后分发二进制、镜像或开放网络服务，要重新检查实际制品/交互义务 |
| **B. 希望自有代码采用 MIT 等宽松许可** | 在没有适用商业授权的前提下，应先移除 PyMuPDF/MuPDF 依赖并重新核验候选及其传递依赖；不能用项目 MIT 覆盖上游 AGPL。独立文件可否另行许可不等于组合应用可仅按 MIT 交付 | **中等，需独立工程批次**：替换解析和所有合成 PDF 工厂，锁定依赖、更新告知与文档，重跑免费后端/PG/浏览器验收 | 如负责人明确要求宽松复用则值得做；本轮不临时替换已经验收的 PDF 链路 | 便于后续宽松复用，但仍须保留新旧依赖的合法告知与历史记录；保留旧 Git 历史不会把旧 PyMuPDF 版本自动变成 MIT |
| **C. Artifex 商业授权** | 需取得覆盖 PyMuPDF、使用主体、源码公开方式、分发范围和未来使用的书面合同；合同外权利与其他依赖义务仍需核实 | **代码成本低、合同/费用待报价**；本轮没有购买或取得授权 | **目前不推荐优先选择**：个人公开作品集不需要保留应用源码秘密，付费收益有限 | 如将来确有闭源交付或不能履行 AGPL 的需求，再向 Artifex 询价并按合同范围维护 |

上述相对成本是基于本仓库调用范围的工程判断，不是已完成替换的工时或质量承诺。Artifex 官方将商业许可按具体使用场景约定，不能从网页推断已获得某项再分发权或固定价格。[Artifex 官方许可说明](https://artifex.com/licensing)

### A 方案需要满足什么

以 AGPLv3 第 1、4～6、13 条为审查依据，保留适用版权与许可告知、提供对应源码和必要构建/安装/运行脚本；覆盖作品以源码或二进制交付时的要求不同。第 13 条针对适用的修改版本与远程网络交互要求显著提供对应源码获取机会；本轮没有公网部署，仍不能把本地、免费或作品展示当作分发许可豁免。组合范围、交互界面告知及第三方条款需由负责人确认，本文不认定所有前后端文件必然构成同一法律作品。[锁定版本包含的 AGPLv3 全文](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING)

AGPL 第 2 条也不意味着经工具处理的用户简历或生成 PDF 自动变成 AGPL 作品；输出是否受覆盖取决于其内容。Artifex 页面建议保留生成/修改 PDF 的 producer 与许可元数据，这一上游指导应随合成 PDF 交付复核，不应误写成用户简历一律必须开源的产品规则。[AGPLv3 第 2 条](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING)、[Artifex producer / copyright 说明](https://artifex.com/licensing)

### B 方案的候选及验收边界

`pypdf` 是可评估的文本解析候选：其[上游许可证为 BSD 三条款](https://github.com/py-pdf/pypdf/blob/main/LICENSE)，官方支持按页提取文本及 layout 模式，同时提示复杂布局、空白和内容流内存问题；不提供 OCR。这个许可事实不证明它与 OfferPilot 当前中文语料等价，也不构成整个依赖图已经与 MIT 相容的结论。另一个可比较的候选是 [MIT 许可的 pdfminer.six](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE)；本轮没有安装或选定版本。[pypdf 官方提取文档](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)

替换验收至少应覆盖：中文/多页/多栏文本与页段引用、加密/损坏/空白 PDF、10 MiB / 50 页 / 文本上限、异常脱敏、处理耗时与内存、私有下载和删除、历史已保存证据不被静默重写、合成样本只生成虚构内容、Mock 白名单及浏览器审核链路。生成中文 PDF 还需单独确认生成器和字体许可；不能为方便生成测试材料而重新引入 PyMuPDF。免费协议桩和 Mock 足以做本批替换回归，无需重新调用付费 DeepSeek。

保留完整开发记录时，旧提交中已存在的 PyMuPDF 集成仍需清楚说明适用授权；未来 MIT 版本不应宣称旧历史及所有第三方代码均获得同一许可。若负责人要求对保留历史作不同授权或对组合范围有疑问，应取得权利人确认或专业意见，不能靠删一个依赖声明解决。

### 推荐与待确认决策

**推荐：先由项目权利人明确接受 A，再按确认范围补齐许可文件和告知后进入公开发布。** 它最能保留 S1～S5-2 已验收成果，也符合免费公开工程作品集的用途。如果负责人必须采用 MIT，则选择 B 并暂缓发布许可结论，完成替换后重审；C 留作未来确需商业授权时的备选。

负责人仍需明确确认：自有代码/文档/截图的权利归属与贡献授权；是否接受 A 的 AGPLv3 发布及后续义务、具体许可版本表述和覆盖范围；如不接受，是否授权开展 B 或取得 C 的合同。**此轮只准备决策，没有添加 LICENSE、授予再许可权、购买授权或宣称法律合规已通过。**

## 直接运行依赖

版本来自锁文件，许可来自对应安装包或 npm 锁元数据；范围声明不代替实际锁定版本。

| 后端依赖 | 锁定版本 | 许可标识/声明 |
|---|---|---|
| Alembic | 1.20.0 | MIT |
| FastAPI | 0.141.1 | MIT |
| OpenAI Python SDK | 2.54.0 | Apache-2.0 |
| PyMuPDF | 1.28.2 | AGPLv3 / Artifex 商业双许可 |
| SQLAlchemy | 2.1.0 | MIT |
| Pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| argon2-cffi | 25.1.0 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| psycopg / psycopg-binary | 3.3.3 | LGPL-3.0-only |

`psycopg[binary]` 会带来二进制组件及其依赖，分发这些包时应保留随包告知，确认 LGPL 及二进制依赖的源码/替换等适用义务；不能把 Python 动态导入解释成自动免除义务。[Psycopg 3 上游仓库](https://github.com/psycopg/psycopg)及安装包 `licenses/LICENSE.txt` 可核对许可证。

| 前端运行依赖 | 锁定版本 | 许可 |
|---|---|---|
| Next.js | 16.4.0 | MIT |
| React / React DOM | 19.3.0 | MIT |
| Radix Dialog | 1.2.0 | MIT |
| Radix Dropdown Menu | 2.1.25 | MIT |
| Lucide React | 1.55.0 | ISC |

## 传递和开发依赖

后端锁文件包含 **55 个第三方包**；Windows 本机安装并核对了其中 54 个相同版本，`uvloop` 是非 Windows 条件依赖，未在此机器安装核验。不能把 Windows 结果当成所有平台二进制包的完整审计。

前端锁文件包含 **636 个包条目**，包括开发工具及其他平台的可选原生包，**不表示本机实际加载 636 个运行依赖**。所有条目均有 `license` 元数据；按元数据统计为 MIT 527、Apache-2.0 40、ISC 23、MPL-2.0 13、LGPL-3.0-or-later 10、BSD-2-Clause 9，其余 14 个为组合许可、BSD-3-Clause、MIT-0、Python-2.0、CC-BY-4.0、CC0-1.0、BlueOak-1.0.0、0BSD 或 MIT/CC0 二选一。元数据齐全不等于完整相容性结论。

应特别保留以下非纯 MIT 告知：

- `certifi` 为 MPL-2.0；`tqdm` 声明 MPL-2.0 AND MIT；其他 Python 包还包含 Apache、BSD、ISC、PSF 等许可。
- Next.js 带来的 `sharp` / `@img/sharp-*` / `sharp-libvips` 含 Apache、LGPL 及组合许可，不能只按 sharp 自身 Apache 许可概括预编译库。[sharp-libvips 上游说明](https://github.com/lovell/sharp-libvips/blob/main/README.md)区分打包脚本与随包共享库。
- `lightningcss` 及各平台原生包、`axe-core` 标记 MPL-2.0；`caniuse-lite` 数据标记 CC-BY-4.0。
- PostgreSQL Docker 镜像及其中操作系统组件有各自许可。本仓库引用官方镜像，不把它重新标为项目自有代码。

当前交付包含源码、锁文件和文档，通过包管理器获取上游依赖，没有把 `.venv`、`node_modules` 或数据库镜像打包进仓库。若未来发布预构建镜像、可执行文件或含依赖的 ZIP，需重新按**实际制品**汇总完整第三方告知、版权、许可证及适用源码提供方式。此轮不将来源链接冒充已完成的二进制分发义务。

## 如何复核

后端安装后，可使用 Python 标准库 `importlib.metadata` 查看每个 distribution 的 `License-Expression` / `License` / `License-File` 与 `files`；锁定版本以 `uv.lock` 为准。前端以 `package-lock.json` 的 `packages[*].license` 和实际 `node_modules/<package>/LICENSE*` / `NOTICE*` 交叉核对。依赖版本变化后应重新审查，特别是 PyMuPDF、psycopg 和原生图像组件。

本次机器可读的本地核对记录位于 Git 忽略目录 `frontend/artifacts/portfolio/dependency-licenses.json`；该路径是本机证据，不是他人克隆后已存在的文件。没有新增依赖、购买许可证或修改上游库。
