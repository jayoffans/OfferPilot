# GitHub 合并前 npm 依赖安全审查

审查日期：2026-10-19。分支：`codex/s5-1-postgresql`。审查基线：`75d741225f8fd138286b602bc43634cb995b70e1`。

结论：已在本地修复 Vitest 的 Critical 和 Moderate 公告，全部依赖审计由 7 项降为 5 项 High；生产依赖审计修复前后均为 0。剩余 5 项是同一个尚无官方补丁的 braces 公告沿开发依赖链传播的结果。ESLint 10 的插件 peer 范围问题仍存在。本报告不作“全部风险已消除”或“可直接合并”的结论。

建议进入 Pull Request 合并审核，由维护者明确评估下述开发工具风险；合并前仍需另行授权推送，并让远程 CI 验证本次新提交。基线的[已通过远程 CI](https://github.com/jayoffans/OfferPilot/actions/runs/39245437296)不覆盖本次依赖修改。本轮仅执行本地诊断、修改、验证和普通提交。

## 1. 诊断方法与计数

先保存 Git 状态及原始清单、锁文件，再执行：

```powershell
cd frontend
npm audit --json --ignore-scripts
npm audit --omit=dev --json --ignore-scripts
npm ls --all --json
npm explain vitest @vitest/mocker eslint-config-next @next/eslint-plugin-next fast-glob micromatch braces
```

同时检查官方公告、npm registry 版本和 peer 元数据，以及已锁定依赖的实现、项目配置和业务调用路径。结论限定于本次查询日期及当前配置。`npm audit` 只反映已收录公告，生产审计为 0 不等于对未知漏洞、供应链或错误部署作安全保证。

| 范围 | 修复前 Critical / High / Moderate / Low / Info | 修复后 | audit 退出码（前 / 后） |
| --- | --- | --- | --- |
| 全部依赖 | 1 / 5 / 1 / 0 / 0，共 7 项 | 0 / 5 / 0 / 0 / 0，共 5 项 | 1 / 1 |
| 仅生产依赖（`--omit=dev`） | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 | 0 / 0 |
| 开发工具链 | 包含全部 7 个受影响包条目 | 剩余 5 个 High 包条目 | 由依赖树及生产排除结果确认 |

这里的 7 项是 npm 的 **7 个受影响包条目、3 个不同 GHSA 公告**，不能理解为 7 个独立漏洞。五项 High 共享同一个根公告；Vitest 条目同时承载 Critical 与 Moderate 公告，npm 按该包的最高严重度计数。

## 2. 七项漏洞逐项记录

表中的“直接 / 传递”相对于 `frontend/package.json`；七个包均属于开发依赖路径。

| 包及修复前实际版本 | 严重度、类型 | 公告编号 | 当前依赖链 | 官方修复与本次处置 |
| --- | --- | --- | --- | --- |
| `eslint-config-next@16.4.0` | High，直接开发依赖 | GHSA-vfj7-8cjw-p6xm / CVE-2026-93687（下游传播） | 项目 → eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces | 无兼容的已发布修复链；保留并记录风险 |
| `@next/eslint-plugin-next@16.4.0` | High，传递开发依赖 | 同上 | eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces | 同上 |
| `fast-glob@3.3.1` | High，传递开发依赖 | 同上 | @next/eslint-plugin-next → fast-glob → micromatch → braces | 最新 3.3.3 仍依赖受影响链，单升不能消除公告 |
| `micromatch@4.0.8` | High，传递开发依赖 | 同上 | fast-glob → micromatch → braces | 当前最新 4.0.8 仍依赖 braces，无安全替代版本 |
| `braces@3.0.3` | High，传递开发依赖 | [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) / CVE-2026-93687 | micromatch → braces | 公告范围 `<=3.0.3`，官方 patched versions 为 None；未修改 |
| `vitest@4.0.18` | Critical，直接开发依赖；另含 Moderate 公告 | [GHSA-5xrq-8626-4rwp](https://github.com/advisories/GHSA-5xrq-8626-4rwp) / CVE-2026-47429；另见下一行公告 | 项目 → vitest；项目 → vitest → @vitest/mocker | Critical 在 4.1.0 / 3.2.6 修复；Moderate 在 4.1.11 修复。采用同主版本 4.1.12，覆盖两项 |
| `@vitest/mocker@4.0.18` | Moderate，传递开发依赖 | [GHSA-82fw-gwwq-j7x9](https://github.com/advisories/GHSA-82fw-gwwq-j7x9) / CVE-2026-84373 | 项目 → vitest → @vitest/mocker | 4.x 修复版本 4.1.11；随 Vitest 统一升级为 4.1.12 |

### braces：深层嵌套模式导致拒绝服务

公告描述递归解析/遍历缺少深度限制，恶意嵌套 brace 模式可能耗尽调用栈，触发未捕获的 `RangeError`，终止 Node 进程。当前官方最新 braces 仍为 3.0.3，尚无补丁。[官方公告](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)

本项目实际可达性：`@next/eslint-plugin-next/dist/utils/get-root-dirs.js` 在配置 `settings.next.rootDir` 字符串/数组时调用 fast-glob；当前 `eslint.config.mjs` 仅使用 Next 的规则集与固定忽略目录，没有设置该选项，默认返回当前工作目录。源码审查未发现简历、JD、URL 或 HTTP 请求输入进入该 glob 链。当前没有确认的生产业务利用路径。这是基于当前配置和调用关系的判断，未用破坏进程的漏洞载荷作动态攻击实验。

开发者或 CI 仍会执行这组包；将来引入动态 glob、恶意仓库配置或向该链传入不可信模式会改变判断。开发依赖身份不能消除拒绝服务风险。

`npm audit` 提出的 `eslint-config-next@14.2.35` 标注 `isSemVerMajor: true`，实际是从 16 跨主版本降级；这不能视为适配 Next 16 的安全补丁。核查最新稳定 `eslint-config-next@16.4.2` / `@next/eslint-plugin-next@16.4.2` 后，后者仍固定 fast-glob 3.3.1，更新这两个包也不能解决问题。[官方插件元数据](https://registry.npmjs.org/@next%2feslint-plugin-next/16.4.2) 本轮没有采用跨主版本降级、非官方 fork、假补丁或审计忽略规则。

### Vitest：UI/API 及 Windows 路径边界

Critical 公告影响 Vitest `>=4.0.0 <4.1.0` 及 `<3.2.6`；条件包括对网络开放 UI/API，或 Windows 上使用 UI/Browser Mode。可越界读文件，具有写测试与重运行权限的 API 还可导致脚本执行。[官方公告](https://github.com/advisories/GHSA-5xrq-8626-4rwp)

当前脚本是 `vitest run`，配置使用 jsdom 和固定测试文件，没有 UI/API/Browser Mode 配置，也没有安装 `@vitest/ui`。Chromium E2E 使用 Playwright，不属于 Vitest Browser Mode。因此未找到当前测试命令触发公告所述服务的路径，但 Windows 开发者启用这些模式后风险会改变。采用 [Vitest 4.1.12 正式版本](https://github.com/vitest-dev/vitest/releases/tag/v4.1.12)消除已知受影响版本，无需依赖操作约定规避漏洞。

### @vitest/mocker：redirect mock 越界读取

Moderate 公告同时影响 Vitest 与 mocker 的 `>=2.1.0 <4.1.11`；5.x 预发布受影响范围为 `>=5.0.0-beta.1 <5.0.0-rc.2`。独立 `mockerPlugin` / `interceptorPlugin` 可通过可达的 Vite HMR WebSocket 注册越界 redirect，并在加载时泄露文件；Vitest 自身 Browser RPC 使用每次运行的 token，不能把它等同于未鉴权独立插件。[官方公告](https://github.com/advisories/GHSA-82fw-gwwq-j7x9)

当前项目没有导入上述独立插件，没有 Vite 应用开发服务器，单元测试也不启用 Browser Mode。未发现该未鉴权路径。Vitest 及其 mocker 已统一到 4.1.12，超过 4.x 的正式修复门槛。

## 3. ESLint 10 peer dependency 兼容性

`eslint-config-next@16.4.0` 自身声明 `eslint >=9`，但它带入的三个已发布插件没有声明 ESLint 10 支持：

| 当前插件 | 官方发布版本的 ESLint peer 范围 | 当前 ESLint 10.12.0 |
| --- | --- | --- |
| `eslint-plugin-import@2.32.0` | `^2 || ^3 || ^4 || ^5 || ^6 || ^7.2.0 || ^8 || ^9` | 不在范围内 |
| `eslint-plugin-jsx-a11y@6.10.2` | `^3 || ^4 || ^5 || ^6 || ^7 || ^8 || ^9` | 不在范围内 |
| `eslint-plugin-react@7.37.5` | `^3 || ^4 || ^5 || ^6 || ^7 || ^8 || ^9.7` | 不在范围内 |

元数据依据：[import 2.32.0](https://registry.npmjs.org/eslint-plugin-import/2.32.0)、[jsx-a11y 6.10.2](https://registry.npmjs.org/eslint-plugin-jsx-a11y/6.10.2)、[react 7.37.5](https://registry.npmjs.org/eslint-plugin-react/7.37.5)。本次查询它们仍是最新稳定发布；上游未发布分支的改动不能代替可安装版本。最新稳定 Next 配置 16.4.2 和 ESLint 10 的补丁更新也不能使这三个 peer 范围支持 10。

Windows / Linux 的 `npm ci` 均退出 0，但仍有 `ERESOLVE overriding peer dependency` 警告；`npm run lint` 通过只证明当前源码检查运行成功，不能证明所有 ESLint 10 插件路径都兼容。`npm ls --all` 仍退出 1，包含 `invalid: eslint@10.12.0`。

本轮保留 ESLint 10.12.0、全部规则与配置，没有禁用 lint，也没有采用 `--legacy-peer-deps` 或强制覆盖 peer。解决方案需要单独评估：等待官方插件支持 10，或单独批准回到三个插件支持的 ESLint 9.39.5 并检查规则行为。后者是跨主版本变更，本轮未执行。重构整个 lint 栈也超出本次最小修复范围。[ESLint 10 迁移说明](https://eslint.org/docs/latest/use/migrate-to-10.0.0)

## 4. 实际修复与锁文件处理

仅修改以下三个文件：

- `frontend/package.json`：Vitest 4.0.18 → 4.1.12；显式固定原本已存在的传递开发依赖 Vite 7.3.7。
- `frontend/package-lock.json`：同步 Vitest 及 7 个 `@vitest/*` 家族包；更新其正式依赖要求的 `es-module-lexer@2.3.2`、`std-env@4.3.0`，并补齐 6 个原 Tailwind WASM 包已声明的 bundled 可选开发依赖记录。
- `docs/GITHUB-DEPENDENCY-REVIEW.md`：本报告。

Vite 并非新引入的运行时组件；固定现有版本是为了阻止重新解析时自动选择 Vite 8。Vitest 4.1.12 支持 Vite 7。没有升级 Next、React、ESLint、TypeScript 或生产依赖的主版本，没有引入大型工具或新增运行时成本。两个传递包的主版本变化来自所选 Vitest 正式版本的明确依赖要求，已通过现有测试验证。

原 npm 10.9.2 在仅更新 Vitest 的锁解析步骤中触发 Arborist 内部 `Cannot read properties of null (reading 'edgesOut')`，在隔离目录也可复现。上游记录了相应 detached-parent peer 解析修复：[npm/cli #8448](https://github.com/npm/cli/pull/8448)。因此仅在隔离解析目录临时调用官方 npm 11.19.1 生成候选锁；先拒绝了会把 Vite 升至 8 的候选，再采用固定 Vite 7 的候选。没有升级系统 npm、Node 或 CI 配置，也没有执行 `npm audit fix --force`。

静态逐对象比较确认：生产依赖清单一致，锁内 100 个非 dev 包记录完全不变，原有 131 个带 OS/CPU 约束的记录完整保留，Vite 整条锁记录不变。锁格式仍为 3。六条新增 bundled 记录属于原 `@tailwindcss/oxide-wasm32-wasi@4.3.3` 的既有声明，父 tarball 与 integrity 未变，均为 dev/optional/inBundle。原 npm 10 在 Windows 和 Linux 实际 `npm ci` 均通过；安装未改变锁文件。

Windows `npm ls --all` 另报 `@img/sharp-wasm32@0.35.5`、`@emnapi/runtime@1.11.3` 为 extraneous。两者及 sharp/平台 wrapper 的锁记录与基线完全一致，原先也存在这些条目；它们来自 Next → sharp 的可选 WASM 平台链，Windows 不安装其 FreeBSD/WebContainers 父 wrapper，但仍落盘通用子包。sharp 此版本没有 install/postinstall 脚本，不能归因为它临时安装的包。现有证据符合 npm 对跨平台 optional 树的剪枝/核算残留，具体 npm 缺陷编号未确认。本轮未通过删锁项或手工删包掩盖该诊断；`npm ci` 成功与 `npm ls` 非零分别记录。

## 5. 验证结果

本机：Windows、Node 22.16.0、npm 10.9.2。Linux 补充验证使用同一版本 Node/npm 的官方 `node:22.16.0-bookworm-slim` 容器；前端源文件只读挂载后复制到临时目录安装，镜像摘要为 `sha256:048ed02c5fd52e86fda6fbd2f6a76cf0d4492fd6c6fee9e2c463ed5108da0e34`。

| 检查 | 本轮实际结果 |
| --- | --- |
| Windows `npm ci` | 退出 0；added 508 / audited 509；上述 peer 警告、5 High 保留 |
| 全部依赖 `npm audit` | 退出 1；5 High，Critical/Moderate/Low/Info 为 0 |
| 生产依赖 `npm audit --omit=dev` | 退出 0；0 漏洞 |
| `npm ls --all` | 退出 1；ESLint peer invalid 与两项可选平台包 extraneous，未标为通过 |
| OpenAPI `npm run api:check` | 退出 0；后端 Schema、保存的 OpenAPI 与生成 TypeScript 一致 |
| TypeScript `npm run typecheck` | 退出 0 |
| ESLint `npm run lint` | 退出 0，当前源码检查通过；peer 兼容问题仍未解决 |
| Windows `npm test` | 13 文件、140 测试通过；0 失败、0 跳过 |
| Production Build `npm run build` | 退出 0；Next 编译、类型检查和 12 个静态页面生成通过 |
| Chromium `npm run test:e2e` | 退出 0；36 测试通过，0 失败、0 跳过，耗时 4.7 分钟 |
| Linux `npm ci` | 退出 0；added 515 / audited 516；同样保留 peer 警告和 5 High |
| Linux `npm test` | 13 文件、140 测试通过；0 失败、0 跳过 |

构建与独立类型检查按顺序执行，随后运行 E2E，避免并发写 `.next`。本轮没有修改后端代码、数据库或认证规则；未重跑完整 SQLite / PostgreSQL 测试，不能将历史 CI 通过数计入本轮。OpenAPI 校验及真实后端隔离 E2E 用于覆盖本次前端依赖变化所涉及的契约和用户流程。E2E 使用临时数据库/文件目录和模拟供应商，没有付费 DeepSeek 调用。

## 6. 生产风险、剩余风险与审核建议

当前 production dependencies 只有既有 Next/React/UI 库，审计没有命中本轮七项，生产版本和锁记录完全未变。源码检查未发现生产路由加载 lint/Vitest/mocker，业务输入也没有进入受影响 glob。当前仅有的 Next API 路由为固定后端代理，不将用户输入解释为测试配置或 glob。由这些证据判断，本轮公告在当前生产业务中未发现可利用路径；这不是对所有可能部署方式的证明。

剩余事项：

1. **5 High 仍在锁中**：同一个 braces DoS 公告，没有官方补丁；需在未来上游发布或另行批准 lint 栈调整后复审。当前固定配置降低可达性，但 CI/开发环境执行这些依赖，风险仍需维护者接受。
2. **ESLint peer 不兼容仍在**：当前 lint 成功不足以替代官方支持范围；未来规则路径或插件更新可能暴露兼容错误。跨主版本调整应独立验证。
3. **工具链残留诊断**：Windows optional WASM 树的 extraneous 状态及 `whatwg-encoding@3.1.1` 弃用提示仍存在，均未通过忽略设置隐藏。弃用提示不是本次七项安全公告，也未据此扩大升级 jsdom。
4. **验证边界**：Linux 仅补充安装和单元测试；完整契约、lint、类型、构建、Chromium 在 Windows 验证。新提交尚未运行远程 Actions；基线绿色 CI 不能替代它。未进行恶意载荷动态利用或公网部署测试。

建议：可以携带本报告进入 Pull Request 合并审核，明确展示两项已修复公告与剩余开发依赖风险。正式合并应以维护者对残余风险的明确判断和本次新提交远程 CI 结果为依据；如果要求 High 清零或完全符合 peer 范围，当前状态不满足该门槛，需要先单独决定兼容方案。

## 7. 本地证据与范围保留

原始 audit JSON、依赖树、安装和验证日志保存在忽略目录 `.local/dependency-review-20261019/`，不将机器路径、缓存或原始日志提交到仓库。锁文件安装前后及 Linux 快照 SHA256 均为 `42BEB018DB458DF1B23B810944E97CFC92421F71E99ECB9D3A71FD3EB9C6B751`。

以下既有本地报告保持原内容与未跟踪状态，不并入本次提交：

| 文件 | SHA256 |
| --- | --- |
| `docs/GITHUB-FIRST-PUSH-ACCEPTANCE.md` | `ABEF6E448F25083036DD2AECE643AE2E9D6D845D9D71ECB6034544A5C0E356FA` |
| `docs/GITHUB-CI-FIX-ACCEPTANCE.md` | `08CC11B401CC16AAC6A4BED11927F44D4D3E6E53072171987D0CF312275E61E4` |
| `docs/GITHUB-SECOND-PUSH-ACCEPTANCE.md` | `86F4C99968FEED503ADF36CEAA968E4963F8AA450C36017FEE2A7C9695D1ADC3` |

本轮没有推送、创建 PR、合并、创建 Release 或公网部署。完成本地提交后停止，等待新的远程操作授权。
