# 第三方依赖与 AGPL-3.0-only 发布审查

审查日期：2026-10-18（Asia/Shanghai）。依据是本轮读取的 `backend/pyproject.toml`、`backend/uv.lock`、`frontend/package.json`、`frontend/package-lock.json`、实际导入、已安装包许可证及下列上游正式资料。范围是**应用源码 Git 仓库**，不包括分发 wheel、`node_modules`、Chromium、镜像或预构建安装包。

正式化续核：2026-10-19。授权来源确认、LICENSE/元数据及界面告知已同步；第三方原文块保持原字节，汇编前言仅更新项目正式许可状态。

## 结论与正式授权状态

**负责人已确认原创内容的授权来源，并正式采用 AGPL-3.0-only，保留 PyMuPDF。现有核查未发现阻止这一源码发布方案的明确依赖许可证冲突。** 2026-10-19 将未经改写的候选转为根目录正式 LICENSE；范围和版权见 [COPYRIGHT](../COPYRIGHT.md)。没有替换解析器、购买商业授权或改变第三方授权；本地准备完成不代表远程发布或二进制/网络服务审查已经完成。

- [LICENSE](../LICENSE)：标准 AGPLv3 全文，未加注、未删节，已正式采用。来源是 [PyMuPDF 1.28.2 官方 COPYING](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING)，34,520 字节，SHA-256 为 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`；项目选择仅版本 3 的声明单列于 COPYRIGHT，不改标准附录。
- [DEPENDENCY-LICENSE-INVENTORY.json](DEPENDENCY-LICENSE-INVENTORY.json)：55 个 Python 与 636 个 npm 锁条目、版本、许可、已装/未装及许可证文件指纹；不含本机绝对路径或安装凭据。
- [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)：本机已安装锁定依赖的版权/许可/NOTICE 原文补充，保留原文，按相同字节去重并注明原始包路径；不等于未来二进制制品的完整告知或源码要约。
- [PYMUPDF-1.28.2-NOTICES.txt](PYMUPDF-1.28.2-NOTICES.txt)：本轮新增的 8 个 PyMuPDF 文件头原文补充，保留 GPL、AGPL 及 pdfplumber 派生部分的 MIT 版权与条款，列出原文件和提取片段的 SHA-256、行数及官方固定版本来源；不改动既有原文集合或清单指纹。

标准全文不单独决定 only / or-later、适用文件及版权人；本项目通过 COPYRIGHT、README、前后端项目元数据明确采用 **AGPL-3.0-only**。不能因为全文末尾“如何采用”的示例含 later，就推断当前应用或全部依赖按 or-later 授权。README 中的“本地作品展示、当前不商业化”只是维护范围，不给 AGPL 增加“禁止商用”或“只限求职”的使用限制。

### 本轮增量复核

2026-10-18 审查的两个锁文件 SHA-256 与当时清单一致，55 个 Python、636 个 npm 锁条目的名称、版本逐项一致；54 个已安装 Python 版本、512 个已安装 npm 版本没有漂移。695 个原文文件指纹引用均通过核对，既有 384 个不同原文的哈希标记均在告知文件中。2026-10-19 仅同步 npm 锁根项目的 license 字段和清单的锁指纹/项目状态；636 个第三方锁条目、Python 锁及上游许可原文块未改，汇编前言仅同步正式状态。正式化后的本轮回归与历史结果分开记录在 [发布准备报告](GITHUB-RELEASE-READY.md)。

保留原有审查边界：Windows 未安装 uvloop，124 个 npm 可选平台条目未安装；另有 25 个已安装 npm 包没有独立的许可文件可供本集合采集，不能把锁元数据或继承自上游项目的许可声明说成已经收到全部制品原文。这些组件未随本次源码 Git 仓库分发，不构成已发现的许可证冲突；未来分发依赖包或构建制品时仍需补足对应告知与源码义务。

## PyMuPDF 的实际组合关系

`PyMuPDF>=1.27,<1.29` 实际锁定并安装为 **1.28.2**；本机 `pymupdf.version` 还报告底层 **MuPDF 1.28.2**。PyMuPDF 不是独立进程或仅测试工具：

- `backend/src/offerpilot_api/services/resume.py` 正式导入 `pymupdf`，检查 PDF 真实性、加密与页数，并调用 `page.get_text("text", sort=True)` 提取正文。
- 页/段偏移、原文和哈希供后续证据契约使用。替换解析器可能改变中文顺序、换行和字符位置，不能无验证地更换。
- `backend/evaluations/pdf_evaluation.py`、本地合成演示、PostgreSQL 专项、E2E 工厂和后端测试也直接生成/读取 PDF。源码没有修改上游库；动态导入、由用户安装、免费或仅本地运行不足以推出公开组合源码没有许可义务。

[官方许可文档](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)说明 PyMuPDF / MuPDF 的 AGPL 与 Artifex 商业双许可，并明确当前文档版本 1.28.2。[Artifex 官方许可说明](https://artifex.com/licensing)要求符合 AGPL 的应用源码披露，并提供不能履行时的商业授权途径；本项目没有取得商业合同。

### 本轮新核实的上游标识差异

本机 PyMuPDF 包 `METADATA` 与 `COPYING` 声称 AGPLv3 / 商业双许可，但 `COPYING` **只有 64 字节的双许可摘要，并非完整正文**；不能把它说成已核对到随 wheel 交付的 AGPL 全文。正式全文因此取自官方版本 tag 的完整 `COPYING`。

同时，[同一 1.28.2 tag 的 `src/__init__.py`](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/src/__init__.py)与安装源码首部另有 `GPL-3.0-only` 标识。MuPDF [1.28.2 源码文件头](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/document.c)则明确 AGPLv3 或后续版本。应保留这些文件级条件，不能把三者悄悄统一成一个 SPDX 标识。

本轮还核对了 [PyMuPDF `src/table.py`](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/src/table.py)：Artifex 部分声明 AGPLv3 或后续版本，移植自 pdfplumber 的部分保留 Jeremy Singer-Vine 2015 年 MIT 版权及完整条款。`_table_headers.py`、`_table_refine.py`、`_table_spans.py`、`_table_union.py` 亦保留 Artifex AGPLv3 或后续版本文件头；`utils.py`、`__main__.py` 保留 Harald Lieder 2020～2022 年版权和 AGPLv3 声明。8 个文件头均与官方 1.28.2 tag 的相应片段逐字节一致，原文见 [独立补充](PYMUPDF-1.28.2-NOTICES.txt)。这些原有条款分别保留；可选择上游允许的版本 3，不因此给 OfferPilot 增加 or-later 授权。

GPLv3 / AGPLv3 第 13 条允许相应 GPLv3 与 AGPLv3 代码组合并传递，原组件各自条款继续有效；因此该标识差异本身尚未显示阻止 AGPLv3 方案的冲突。**差异没有由 OfferPilot 解决。** 本次按原有条款保留这些标识和版权；若需要上游统一授权解释、后续闭源授权或超出本次源码发布范围，应向 Artifex 取得明确答复。[AGPLv3 正式文本](https://www.gnu.org/licenses/agpl-3.0.html)及[官方上游全文副本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.28.2/COPYING)

GNU 官网本轮浏览与直连请求均超时，未将访问失败说成成功；条款正文使用 PyMuPDF 官方固定版本副本阅读并保存，GNU 页面保留为正式文本入口。

## 正式授权后的源码与发布义务

| 场景 | 对本项目的具体要求与当前状态 |
|---|---|
| 自有源码公开（第 1、4、5 条） | 来源已由负责人确认，完整 LICENSE 已正式采用；COPYRIGHT 明确范围/第三方边界，保留原有版权与无担保告知。本轮只作本地提交。 |
| 对应源码（第 1 条） | 源码交付的对应源码是同一作品源码。仓库已有前后端源代码、固定 Prompt、OpenAPI 及生成/校验脚本、迁移、本地初始化/合成数据、测试、锁文件与运行说明。私钥、个人数据和私有环境文件不是需公开的源码。 |
| 交互告知（第 0、5(d) 条，含其适用例外） | 既有工作台及登录/注册页脚共用版权、无担保、依条款修改再分发、项目源码/许可全文/第三方入口；本地 /LICENSE.txt 与根 LICENSE 字节一致。 |
| 分发目标代码/镜像（第 6 条） | 必须按实际制品提供对应源码、构建/安装所需信息、适用告知及源码获取安排。若依赖为程序特别需要的动态库，不能仅以动态链接排除对应源码范围。锁文件和上游首页链接本身不能替代适用的第 6 条义务。当前不分发这些制品。 |
| 远程交互（第 13 条） | 对适用的修改版本，显著提供用户免费获取其对应源码的机会；应指向实际运行版本及必要组成。不能只以“库没改”认定集成应用免除。当前不部署公网，也不声称已完成网络服务合规。 |
| 下游权利（第 7、10 条） | 不额外禁止商业使用、修改或再分发；商标、第三方许可、真实用户数据与源代码授权应区分。 |

界面使用“© 2026 OfferPilot 各贡献者”作为各权利人的集合署名，不是猜测真实姓名、新设组织或声称版权转让。项目源码/第三方链接指向已确认仓库的功能分支，本轮尚未推送，远程可用性和实际运行版本对应源码须在发布后核对；不以 UI 链接单独证明未来网络服务已合规。

源码完整性与可重复构建是不同结论：运行依赖由锁文件固定；`backend/pyproject.toml` 中构建引导仍是 `setuptools>=70`，没有在 runtime `uv.lock` 精确锁定，本次环境也没有可供本审查读取的 setuptools distribution。Python、uv、Node/npm、操作系统及 PostgreSQL 镜像组件是外部工具/依赖。本轮不宣称整个跨平台二进制供应链逐项锁定或审计完毕。

## 直接运行依赖

| 后端依赖 | 锁定版本 | 原有许可与注意事项 |
|---|---|---|
| Alembic | 1.20.0 | MIT |
| FastAPI | 0.141.1 | MIT |
| OpenAI Python SDK | 2.54.0 | Apache-2.0；仅 SDK 许可，不替代 DeepSeek 服务条款 |
| PyMuPDF | 1.28.2 | 包级 AGPLv3 / 商业双许可；文件级 GPL-3.0-only 差异见上文 |
| SQLAlchemy | 2.1.0 | MIT |
| Pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| argon2-cffi | 25.1.0 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause；本审查按 BSD 路径评估相容，保留包内两份文本；原生 OpenSSL 另有条款 |
| psycopg / psycopg-binary | 3.3.3 / 3.3.3 | LGPL-3.0-only；二进制包含 libpq 等组件，不是全部变成项目 AGPL |

| 前端运行依赖 | 锁定版本 | 原有许可 |
|---|---|---|
| Next.js | 16.4.0 | MIT；`dist/compiled` 还有内嵌依赖，随包告知另行保留 |
| React / React DOM | 19.3.0 / 19.3.0 | MIT |
| Radix Dialog | 1.2.0 | MIT |
| Radix Dropdown Menu | 2.1.25 | MIT |
| Lucide React | 1.55.0 | 包级 ISC；源自 Feather 的列举图标还带 Cole Bemis 的 MIT 条款，完整保留 |

前端开发依赖也在机器可读清单中逐项记录，包括 Playwright 1.64.0（Apache-2.0）、Tailwind CSS 4.3.3（MIT）、TypeScript 5.9.3（Apache-2.0）、ESLint 10.12.0（MIT）、Vitest 4.0.18（MIT）、jsdom 26.1.0（MIT）及 OpenAPI TypeScript 7.13.0（MIT）。Playwright 下载的 Chromium 不在 Git 源码交付中，不把工具包 Apache 许可套到浏览器全部组件。

## 全部 Python 锁条目

共 55 个第三方条目；本机 Windows 安装 54 个，版本与锁文件逐项相符。uvloop 在 Windows 未安装，只核对其官方固定版本 README 及两份许可，不能声称已审计 Linux wheel。

| 包 | 版本 | 原有许可 |
|---|---|---|
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| argon2-cffi-bindings | 26.1.0 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| distro | 1.9.0 | Apache-2.0 |
| dnspython | 2.8.0 | ISC |
| email-validator | 2.3.0 | Unlicense |
| fastapi | 0.141.1 | MIT |
| fastapi-cli | 0.0.32 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD-3-Clause |
| jiter | 0.17.0 | MIT |
| mako | 1.4.3 | MIT |
| markdown-it-py | 4.2.0 | MIT |
| markupsafe | 3.0.3 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT |
| openai | 2.54.0 | Apache-2.0 |
| psycopg | 3.3.3 | LGPL-3.0-only |
| psycopg-binary | 3.3.3 | LGPL-3.0-only |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pydantic-extra-types | 2.11.1 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pymupdf | 1.28.2 | AGPL-3.0 / Artifex commercial (package); GPL-3.0-only (src/__init__.py header) |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pyyaml | 6.0.3 | MIT |
| rich | 15.0.0 | MIT |
| rich-toolkit | 0.20.5 | MIT |
| shellingham | 1.5.4 | ISC |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| sqlalchemy | 2.1.0 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| typer | 0.27.2 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.22.1 | MIT OR Apache-2.0 |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.1 | BSD-3-Clause |

uvloop 0.22.1 的 [README](https://github.com/MagicStack/uvloop/blob/v0.22.1/README.rst)明确 MIT / Apache 双许可，本审查可选 MIT 路径，原始两份文本已保存。其 libuv 等捆绑组成仍属于未来 Linux 二进制制品审查边界。

## npm 传递依赖与多许可证处理

636 个非根条目均有锁文件 `license` 元数据；其中本机有 512 个条目的目录和匹配版本，**124 个条目未安装**（多为其他平台可选包）。本清单按锁文件范围，不把安装目录中额外残留的包当作本次发布锁定依赖。锁元数据统计：MIT 527、Apache-2.0 40、ISC 23、MPL-2.0 13、LGPL-3.0-or-later 10、BSD-2-Clause 9；其余 14 个为多许可表达式、BSD-3-Clause、MIT-0、Python-2.0、CC-BY-4.0、CC0-1.0、BlueOak-1.0.0、0BSD 等。完整名称和版本见 [JSON 清单](DEPENDENCY-LICENSE-INVENTORY.json)，不是只审了直接依赖。

| 重点组件 | 原有许可 | 本次处理与相容性边界 |
|---|---|---|
| `certifi` 2026.7.22、`tqdm` 4.70.1 | MPL-2.0；MPL-2.0 AND MIT | AND 的两套义务均保留，不能只选择 MIT；原库/文件继续按原条款授权。 |
| `lightningcss` 及各平台包 1.32.0、`axe-core` 4.14.0 | MPL-2.0 | MPL 的文件级义务、源码可得性和更大作品组合规则继续适用；本机可读源码未发现额外 Secondary License 不相容声明，许可证附件 B 的示例本身不等于附加该声明。未装平台包仍只核对元数据。 |
| `sharp` 0.35.5、`@img/sharp-libvips-*` 1.3.4、平台 `@img/sharp-*` 0.35.5 | Apache-2.0、LGPL-3.0-or-later，以及 AND 组合；Wasm 另含 MIT | AND 必须全部满足。当前 Windows x64 组件报告 libvips 8.18.7 及多种本地库；其根 LICENSE 只有 Apache 文本，不能据此认定 LGPL/所有原生依赖告知齐全。未分发原生包，未来分发必须按真实平台、共享/静态链接方式、许可与源码/替换义务重审。 |
| `caniuse-lite` 1.0.30001815 | CC-BY-4.0 | 是数据许可；保留版权、归属、许可链接及修改说明，不将数据改标 AGPL。原文已保存。 |
| `argparse` 2.0.1 | Python-2.0 | 保留 Python 原始版权和许可链，不能简写成 MIT。 |
| `minimatch` 10.2.6、`tslib` 2.8.1 | BlueOak-1.0.0；0BSD | 宽松条款，不据此改变组件原许可。 |
| `sniffio` 1.3.1、`type-fest` 4.41.0 | MIT OR Apache-2.0；MIT OR CC0-1.0 | OR 允许选择其中一条；本审查均按 MIT 路径评估，仍保留随包原文，不改上游表达式。 |

MIT / ISC / BSD 等允许组合也有各自告知条件；Apache-2.0 还涉及专利及适用 NOTICE。MPL、LGPL 与 AGPL 的组合不意味着依赖被自动重新授权。对 MPL 更大作品与 Secondary Licenses 条件，参见 [Mozilla 官方 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)；对 psycopg，依据 [3.3.3 正式 LICENSE](https://github.com/psycopg/psycopg/blob/3.3.3/LICENSE.txt)，不用 psycopg2 文档代替 psycopg 3 的实际许可；对 libvips 预构建库，依据 [sharp-libvips 1.3.4 官方说明](https://github.com/lovell/sharp-libvips/blob/v1.3.4/README.md)。

本次原文集合还单独采集了 Next.js `dist/compiled` 下 **131 份**许可/告知文件，避免将 Next 自身 MIT 概括为全部内嵌依赖。总共保存 **384 段不同字节的原文**，原文件 SHA-256 与来源路径可在 JSON 中交叉复核。原文中的公开上游版权人、联系方式属于合法归属材料，应保留；不能为了项目身份隐私把这些必需版权信息一起抹除。

## 图标、字体、图片与 PDF

- UI 图标（包括品牌纸飞机）从 `lucide-react` 导入；[Lucide 官方许可](https://lucide.dev/license)和安装包 LICENSE 含 ISC 及 Feather 派生图标 MIT 告知，已完整保存。Radix 用于对话框/菜单。
- `frontend/src/app/globals.css` 仅列系统字体名 Segoe UI / PingFang SC / Microsoft YaHei / sans-serif。仓库没有字体二进制、`next/font` 下载或第三方网页字体；列名称不等于分发字体文件。
- 当前跟踪图片只有 `docs/images/demo/` 内 11 张本产品真实页面截图；未发现外部摄影、插画、股票素材或另行获取的字体文件。截图使用合成演示数据；隐私内容由独立历史/图像审查复核，负责人已确认有权授权其原创内容，图中第三方图标仍保留原许可。
- 跟踪的合成 PDF 由本仓库 `backend/evaluations/pdf_evaluation.py` 创建。该 PDF 实际字体为 `Heiti` / `china-s`，`get_fonts` 标记 `n/a`，没有嵌入字体程序；[PyMuPDF Page.insert_font 官方说明](https://pymupdf.readthedocs.io/en/latest/page.html#Page.insert_font)区分该内建 CJK 引用和 `Font("cjk")` 可能嵌入的字体，不能混为“随 PDF 打包系统字体”。
- PDF 元数据的作者/标题是明确的合成评测标记，producer 当前为空；[Artifex](https://artifex.com/licensing)关于保留 producer/许可元数据的表述是上游建议，不能伪装成在标准 AGPL 文本之外已确定的新增强制条款。本轮没有因此改写样本。AGPL 第 2 条也不使用户上传的简历自动开源；是否覆盖工具输出取决于输出本身的内容。

## 不在本次源码发布结论内的制品

PyMuPDF wheel 内 MuPDF 的第三方库（上游 [thirdparty 目录说明](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/thirdparty/README)明确有多个子模块）、psycopg-binary 的 libpq / SSL、cryptography 的 OpenSSL、sharp / libvips 各平台依赖、PostgreSQL / OS 镜像及 Chromium 都有独立版权和条款。本次只列入已核对证据和明确边界，不声称已完成这些原生制品的完整源代码/静态链接/二进制再分发审查。

当前 Git 仓库不包含上述下载包、`.venv`、`node_modules`、镜像、`.next` 或浏览器安装目录。若后续把任何这些组件加入 ZIP、Release、Docker 镜像、可执行文件或静态浏览器 bundle，须在交付前按真实内容重新生成告知、提供必要对应源码并履行 LGPL / MPL 等义务；本次文档不能直接复用为“所有平台制品合规证明”。

## 已确认决定与后续动作

1. 负责人已明确确认除已列第三方/生成内容外，项目代码、文档与合成截图均为其组织创作且有权授权，无未授权雇主/学校/第三方例外；记录于 COPYRIGHT。Git 占位身份本身不作为权利链证明。
2. 已选择保留 PyMuPDF、优先采用 AGPL-3.0-only；保持上游文件级许可和版权原文。若需要消除上游标识差异、后续闭源授权或分发原生制品，向 Artifex 核实相应范围。
3. 已将候选转为正式 LICENSE、补齐 COPYRIGHT/页脚告知与源码入口，同步软件包许可元数据；实际验证和剩余远程步骤见发布准备报告。
4. 保留依赖原有文本和 NOTICE；版本、素材或分发方式变化时重审。直接/传递依赖没有因应用采用 AGPL 而被自动重标。
5. 本轮只准备源码仓库发布；普通历史路径已经获准保留，真实凭据扫描继续执行，远程写入仍须后续明确授权，见 [本轮发布准备](GITHUB-RELEASE-READY.md)。
