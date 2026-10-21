# 求职作品集最终本地验收

日期：2026-10-21（Asia/Shanghai）。目标是招聘展示、本机复现和面试讲解，不增加求职业务、AI Agent 能力或技术栈。

**结论：本地作品集收尾完成，可作为已实现、可复现的求职项目使用。** Windows 一键入口、后端、真实 PostgreSQL、前端、构建、Chromium 和重复启动均已实际验收；依赖、许可及平台风险继续保留。本轮结束于本地独立分支，不自动合入 main。

## 1. 基线与变更边界

- 远程 main 基线：`ceb3014bf4b225dbeb0ebfd66ae0fc21469f1ece`。
- 本地独立分支：`codex/portfolio-final-polish`，从最新 origin/main 建立，保留已合并的展示修订和页脚修复。
- [main Actions 39553336784](https://github.com/jayoffans/OfferPilot/actions/runs/39553336784) 的 SQLite、PostgreSQL、Frontend / Chromium 三项实际成功。它只证明基线，本地新增启动器与检查修订没有推送，未取得本分支远程 CI。
- 不改业务 API、领域规则、Schema、数据库迁移、依赖版本、锁文件、LICENSE、截图或 Git 历史。不新增 RAG、Agent、浏览器代填、自动申请或公网部署；付费模型调用 0 次。
- 开工前的 7 份未跟踪 GitHub 验收报告保留，不删除、覆盖、stash 或误加入本轮提交。

## 2. 实际修改文件

| 文件 | 修改目的 |
|---|---|
| `start-demo.cmd` | Windows 双击入口，从任意目录定位仓库，调用现有 Python 工具 |
| `scripts/local/manage.py` | 增加 `demo` 薄编排：锁定安装、独立 SQLite profile、复用初始化/播种、就绪等待和本次进程树清理；旧命令保留 |
| `tests/test_local_launcher.py` | 验证端口、外部路径、已有数据、失败中止、超时及 Windows 清理边界 |
| `frontend/scripts/check-contract.mjs` | 仅归一 Windows CRLF，再比较已生成类型内容，避免把换行当成契约漂移 |
| `README.md` | 前移产品截图，突出四项工程亮点、Windows 入口、短片分镜和当前基线 CI |
| `docs/LOCAL_SETUP.md` | 一键启动、私有凭据、重复启动、停止、前置工具与 Windows 保留端口排查 |
| `docs/TESTING.md` | 新启动器测试入口、契约换行边界、当前远程基线与本轮验证范围 |
| `docs/DEMO.md` | 增加真实界面的 7 镜头 / 80 秒分镜，保留原 8～10 分钟路线 |
| 本报告 | 汇总本轮实际结果、失败复验、未运行项和剩余风险 |

页脚修复已经存在于最新 main，本轮没有重复修改 React 组件。构建/开发工具生成的临时产物不作为业务改动提交。

## 3. 启动工具选型与隔离设计

先评估已有实现，再决定复用项目脚本：

| 实现 | 维护与许可核查 | 本项目集成成本与结论 |
|---|---|---|
| [concurrently](https://github.com/open-cli-tools/concurrently) | 支持 Windows，MIT；[v10.0.6](https://github.com/open-cli-tools/concurrently/releases/tag/v10.0.6) 于 2026-10-08 发布 | 需要新增 npm 工具和锁记录，仍不能代替数据库/profile、凭据、健康检查和数据保留逻辑；不引入 |
| [just](https://github.com/casey/just) | [CC0](https://github.com/casey/just/blob/master/LICENSE)；[1.59.0](https://github.com/casey/just/releases/tag/1.59.0) 于 2026-10-15 发布 | 需要额外安装任务运行器和配置，同样需要项目隔离编排；不引入 |
| 现有 `manage.py` + CMD | Python 标准库与 Windows Job Object；没有新增依赖 | 复用 init/seed/API，只增加本地进程编排与停止边界，采用此方案 |

默认 `start-demo.cmd` 使用 `.local/windows-demo/`，与旧 `demo`、现有 PostgreSQL 和开发库分开。首次按锁安装后创建 SQLite、迁移 head、播种合成资料并启动 API/Web；已有 seed 不重复播种、不还原用户编辑，不隐式迁移已有库。

后端演示子进程不加载开发者 `.env`，当前前端服务配置由 loopback 地址显式覆盖；Mock 开启、真实 DeepSeek 关闭。密码只保留在忽略目录，入口打印查看路径而非密码。端口或 Next 开发锁被占用即拒绝，不复用其他进程；子进程安装/初始化与就绪等待有上限。Windows Job 只拥有本次创建的进程树，退出时不按进程名称或外部 PID 杀服务。

启动：`.\start-demo.cmd`；复用已锁定安装：`.\start-demo.cmd --skip-install --no-browser`。新独立演示可用 `--profile interview-demo`。按 Ctrl+C，若 CMD 提示 `Terminate batch job (Y/N)?`，输入 Y；数据保留。

## 4. README、页脚和演示检查

README 开头保留业务价值、技术栈和事实审核流程，将真实产品图前移，明确四项有代码/验收依据的工程设计：候选与事实隔离、可解释四态匹配、并发/幂等/恢复、用户隔离与数据生命周期。未把 Mock、手工投递或单样本真实评测描述为自动海投、模型总体准确率或生产成绩。

11 张既有截图存在，字节数及 SHA-256 与 manifest 一致；没有重新生图、改图或重新收录 E2E 产物。页脚项目源码指向默认 main 仓库，第三方声明显式指向 `blob/main`；AGPL-3.0-only、版权和无担保文案保留。`frontend/public/LICENSE.txt` 与根 LICENSE 字节级一致，SHA-256 为 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`。真实登录页 HTTP 验收再次确认页脚、第三方 main 链接及许可文件。

[80 秒分镜](DEMO.md#招聘展示短片分镜80-秒)分为工作台、手工画像、pending Mock 证据、审核 JD、规则匹配、人工下一步/时间线、本人导出七段。所有镜头基于真实界面和合成种子，凭据及密码操作不入镜；异步等待如剪辑须标明切点。仅准备分镜，没有录制或伪装成已完成视频。

## 5. 本轮实际执行结果

环境：Windows，Python 3.11、Node 22.16.0、npm 10.9.2；Docker Engine 29.8.1，PostgreSQL 使用仓库固定 18.6 镜像摘要。

| 检查 | 实际结果 |
|---|---|
| `uv run --locked --project backend python -B -m unittest discover -s tests -v` | 退出 0；666 项，53 项未配置 PostgreSQL 而 skip，其余通过；不把 skip 计为 PostgreSQL 验证 |
| `... -m unittest tests.test_local_launcher -v` | 退出 0，新增 12 项通过；包括真实 Windows 孙进程清理及无关进程保留 |
| HTTP 文件 / 账户生命周期 smoke | 两项命令均退出 0 |
| `npm.cmd --prefix frontend ci` | 退出 0，锁定安装成功；5 High、peer 和既有 deprecation 警告保留 |
| OpenAPI / 生成类型检查 | 首次 CRLF 误报，修正换行比较后退出 0，后端 JSON 与生成类型保持一致 |
| ESLint | 退出 0；不据此声称 peer 范围已兼容 |
| Vitest | 13 个文件、140 项通过 |
| `next build` | 退出 0，生产构建通过，无公网部署 |
| TypeScript | build 后执行，退出 0 |
| Playwright Chromium | 36 / 36 通过，2.8 分钟；真实 API/Web、权限、并发、故障、持久恢复与业务闭环 |
| 截图检查 `collect_demo.py --check` | 退出 0，11 张 manifest 一致 |
| Windows 入口真实首次启动 | 默认入口执行锁定安装、独立 SQLite 初始化与合成播种；API/Web 就绪，真实代理登录及页脚检查通过 |
| 同一 profile 重启 | `--skip-install --no-browser` 成功；3 条申请、1 条教育、2 条技能、7 项待审 Mock 候选可读取，保存过的合成备注及凭据保留 |
| Ctrl+C 清理 | 两次均释放 8000 / 3000，Next 开发锁删除；E2E 的 8001 / 3001 也已释放 |
| 已有数据保护 | 开工前记录的 21 份已有环境配置/数据库文件哈希全部未变 |
| PostgreSQL 专项 | 53 / 53 通过，0 skip，退出 0，81.567 秒；含真实固定名备份恢复和 Docker 日志边界 |
| PostgreSQL API / 领域回归 | 原断言 292 / 292 通过，0 失败/错误/skip，退出 0，348.857 秒；基于 TestClient，不计为真实网络 HTTP |
| 文档专项 | README 及直接导航 26 份 Markdown、375 处引用；缺失路径/锚点 0，18 处历史本机产物例外单列保留；不宣称所有外部页面都已联网探测 |
| Git 差异 / 秘密审查 | 9 个明确文件，空白检查通过；启发式扫描及新增差异人工复核，改动文件没有敏感信息命中 |

日志和脱敏 JSON 保留在系统 TEMP，私有演示状态保留在忽略的 `.local/windows-demo/`；不把日志、数据库、凭据或 E2E 原始产物加入仓库。

### 失败、复验与未运行项

1. 标准 PostgreSQL 入口首次失败：Windows 将 55432 包含在 `55382–55481` 保留范围，Docker 端口绑定被拒绝；没有修改现有 `.env.s5-postgres` 或开发卷。
2. 首轮随机名隔离 PostgreSQL 的 53 项专项中，51 项通过、1 项日志检查 skip、1 项恢复演练 error。原因是原备份工具和日志检查要求固定隔离容器名；没有改变安全约束、跳过失败断言或把这一轮写成通过。随后用空闲 loopback 45432 及固定名全新临时实例复验，53 专项与 292 回归全部通过。原停止容器按 ID 暂存后恢复，完整 ID、全部 State / Mounts 与前快照精确一致，仍为停止态；原命名卷未启动、未改动，`.env.s5-postgres` 哈希前后相同。本次临时容器及匿名卷已删除，45432 已释放。系统 55432 保留状态没有被修改。
3. 契约检查首次失败：Git 工作树生成类型为 CRLF，生成器为 LF；只修正检查工具的换行归一，没有重生成或改动 API/type 内容。原 JSON 相等及生成类型内容检查保留。
4. 临时启动验收器的首轮登录多带了合成凭据文件元数据，API 返回 422；仅将临时请求限定为 email/password 后，真实代理登录和后续验收通过。
5. 未运行真实付费 DeepSeek 扩样、macOS/Linux 的本分支启动器验收、PostgreSQL Chromium、PostgreSQL 真实网络 HTTP smoke、100 用户负载、独立完整灾难恢复/生产容量验证、公网部署或当前分支远程 CI。既有 main CI、PostgreSQL TestClient 回归和本机 SQLite Chromium 不能替代这些项目。

## 6. 依赖与许可风险复核

2026-10-21 实际运行：全量 `npm audit --json --ignore-scripts` 退出 1，5 High，其余严重度为 0；`--omit=dev` 退出 0，审计范围内 0 告警。两个端点均成功返回 JSON，不把网络失败当成零结果。

5 项是同一个 [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) 沿开发链传播：`eslint-config-next@16.4.0 → @next/eslint-plugin-next@16.4.0 → fast-glob@3.3.1 → micromatch@4.0.8 → braces@3.0.3`。官方补丁仍为 None。当前未确认生产业务输入到该 glob 链的路径，开发工具 DoS 风险仍需维护；没有执行 audit fix、跨主版本降级、强制覆盖或禁用 lint。

`npm ls` 退出 1，ESLint 10.12.0 与当前 import / jsx-a11y / react 插件 peer 范围仍不兼容；上游最新正式插件 peer 也未覆盖 10。另保留两个既有 optional extraneous 条目：`@emnapi/runtime@1.11.3`、`@img/sharp-wasm32@0.35.5`。当前 642 条锁记录中实际安装 508 条、134 条未安装，已安装锁定版本漂移 0；没有升级或新增依赖。

第三方许可库存仍是 636 条旧快照，与当前锁的 642 条不完全同步；变更项许可与 bundled 告知待补。原 AGPL-3.0-only 与上游版权告知保持，不宣称全量许可库存或未来二进制制品已经审查完毕。生产 audit 为 0 不代表未知漏洞、供应链或错误部署没有风险。

## 7. 简历与面试使用结论

可以作为已实现、可本地复现的 Python 后端 / AI 应用项目写入简历，围绕上面的四项工程设计和真实操作讲解。项目已有公开 main；本轮补充仍只在本地独立分支，不冒充已经发布的远程功能。

个人贡献和熟练程度须按本人实际参与核对；应能结合服务代码及反例测试解释取舍。不要写“独立完成全部功能”“生产大规模用户验证”“模型准确率达到某总体指标”“自动海投”或已公网商业上线。真实 DeepSeek 仍只有一个授权合成样本；未实现功能和已有安全、许可及平台边界继续保留。

## 8. Git、安全与停止点

最终差异限定为第 2 节的 9 个文件。`git diff --check`、暂存区空白检查、Python AST 及独立代码复核通过；没有业务代码、前端功能、生成 API 类型、迁移、依赖、锁文件、LICENSE 或截图变化。Next 构建/开发引起的生成文件差异未纳入本轮变更。

仓库启发式安全扫描实际完成，历史合成夹具、占位凭据和正常本机路径仍产生待人工判断的命中；未删除告警或重写历史。新增/改动文件命中为 0，人工核对没有真实 Key、联系方式、个人简历或意外文件。该检查不包含 OCR/全部外部副本，不写成全仓绝对安全保证。

原 7 份本地 GitHub 报告与开工前 SHA-256 全部一致，仍未跟踪；没有将它们一起暂存。本轮仅在 `codex/portfolio-final-polish` 保存本地提交，消息为 `chore: finish local portfolio demo and verification`，具体 SHA 可由 `git log -1` 查询。本报告随本轮改动保存，不为了写入自己的提交 SHA 追加提交。

任务在本地独立分支结束，不推送、创建/合并 PR、更改 main 或远程设置。运行过的 API/Web 与临时测试容器均已停止/清理，私有合成演示保留供本人复现。
