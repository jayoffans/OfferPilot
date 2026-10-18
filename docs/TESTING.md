# 测试与 CI

测试使用合成数据、隔离临时库和无网络 Mock / Fake Provider。没有真实付费 DeepSeek 调用。真实历史模型评测不属于 CI，不能用固定响应的测试通过率表示模型准确率。

## 安装和入口

先在仓库根目录执行：

```sh
uv sync --project backend --locked
npm --prefix frontend ci
```

Windows PowerShell 如因执行策略不能直接运行 npm / npx，改用 `npm.cmd` / `npx.cmd`。`uv run --project backend --locked python` 等价于已同步的 `backend/.venv` Python，避免误用系统解释器。

| 检查 | 仓库根目录命令 | 主要范围 |
|---|---|---|
| 后端单元及 SQLite 集成 | `uv run --project backend --locked python -B -m unittest discover -s tests -v` | 规则、Schema、认证/对象归属、文件、任务、画像、岗位/匹配、申请/工作台、导出/注销、迁移；含单元与临时SQLite集成，不宣称全是纯单元 |
| 真实 HTTP 文件生命周期 | `uv run --project backend --locked python -B tests/http_file_lifecycle_smoke.py` | HTTP 登录、上传/下载/删除、文件生命周期 |
| 真实 HTTP 账户生命周期 | `uv run --project backend --locked python -B tests/http_account_lifecycle_smoke.py` | 重验密码、导出 ZIP、注销、撤销后的访问 |
| PostgreSQL 专项与 API 回归 | `uv run --project backend --locked python scripts/local/postgres_tests.py` | 自动启动已有 Compose，角色分离、新库迁移、真实并发/锁、错误日志、导出/注销，复用原 API 断言 |
| 前端测试 | `npm --prefix frontend test` | Vitest / React Testing Library，交互、冲突、来源文案和代理 |
| OpenAPI 契约 | `npm --prefix frontend run api:check` | 真实 FastAPI Schema、保存的 OpenAPI、生成 TypeScript 三方一致 |
| ESLint | `npm --prefix frontend run lint` | 前端静态检查 |
| Next 构建 | `npm --prefix frontend run build` | 本机生产构建，不代表公网部署 |
| TypeScript | `npm --prefix frontend run typecheck` | TypeScript 类型，包括 Next 生成的路由类型；干净环境先运行 build |
| Chromium E2E | 见下一节 | 真实浏览器及真实后端闭环、持久恢复/故障和跨用户边界 |
| 合成本地环境 | `uv run --project backend --locked python scripts/local/manage.py status` | 本机种子登录、health、正式画像、申请、明确 Mock 的待审候选；先按 LOCAL_SETUP 初始化/播种 |

未配置 PostgreSQL 的普通 unittest 中，`tests/postgres` 明确 skip；这不表示 PostgreSQL 已验证。应单独执行 PostgreSQL 入口。需要只跑一个层次时：

```sh
uv run --project backend --locked python scripts/local/postgres_tests.py --suite specific
uv run --project backend --locked python scripts/local/postgres_tests.py --suite regression
```

PostgreSQL 入口沿用 `tests/postgres/support.py`：每个用例新建随机测试库、迁移 head、用受限 `s5_app` 执行业务断言，最后仅清理自己创建的测试库。不会迁移、清空用户传入的数据库。随机密码仅在忽略的 `.env.s5-postgres` 和子进程环境中传递，不写 CI 配置。API 回归摘要默认写入忽略的 `frontend/artifacts/portfolio/pg-regression.json`。

## Chromium E2E

在 `frontend` 目录执行（Linux CI 加 `--with-deps`）：

```sh
npx playwright install chromium
npm run test:e2e
```

E2E 自行创建临时 SQLite 环境、迁移0010，启动真实 FastAPI 和 Next 服务，使用真实身份/文件/画像/岗位/申请/账户 API；固定合成抽取和故障注入单独标记测试。没有向真实 DeepSeek、招聘网站或招聘方发请求。结束时关闭服务并清理临时库和文件；清理失败使测试失败。

默认 API 8001、Web 3001 必须空闲；可用 `OFFERPILOT_E2E_API_PORT` / `OFFERPILOT_E2E_WEB_PORT` 修改。Python 默认仓库 venv，特殊路径设置 `OFFERPILOT_E2E_PYTHON`。构建、E2E 和本地 Next 开发服务都可能写 `.next`，应按顺序运行。截图是主动选定的合成页面，密码/邀请码/Cookie不截图、不写 trace，运行输出在忽略的 `frontend/artifacts/`。

可选在 PostgreSQL 上运行同一 Chromium 套件（先安装前端依赖及 Chromium）：

```sh
uv run --project backend --locked python scripts/local/postgres_tests.py --suite specific --e2e
```

该命令先完成真实 PostgreSQL 专项，再以 `OFFERPILOT_E2E_POSTGRES=true` 启动原有 E2E 隔离器。没有悄悄改用 SQLite 来让 PostgreSQL 检查通过。

## 更新 API 契约

后端公开 Schema 发生变化时运行：

```sh
uv run --project backend --locked python frontend/scripts/export-openapi.py
npm --prefix frontend run api:generate
npm --prefix frontend run api:check
```

生成文件应随 API 变更一起提交；不要手工维护第二套会漂移的客户端 Schema。导出只读取 OpenAPI，不迁移数据库或读取用户记录。

## CI 的实际范围

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 包含三项并行任务，均按锁文件安装、限制总时长、只读 GitHub 权限，不访问仓库秘密，也不启用真实 DeepSeek：

1. `backend`：SQLite 全回归、真实 HTTP 文件和账户生命周期、全新 SQLite 本地种子、重复种子和状态检查、公开合成截图清单/哈希完整性检查。
2. `postgres`：Compose 真实 PostgreSQL 专项及原 API 回归、全新 PostgreSQL 本地种子和状态检查。
3. `frontend`：OpenAPI 契约、ESLint、Vitest、Next build、TypeScript、Chromium 全套。**CI 的浏览器层默认使用 SQLite**；PostgreSQL 浏览器检查保留上述可复现的本地入口，不在每个 PR 中重复整套浏览器工作。

工作流仅为 PR、main/master 的未来 push 和手动触发配置，没有部署、发布或推送步骤；Actions 固定到官方仓库已核实的提交 SHA，uv 固定0.12.19。采用的 action 依据：[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)、[setup-node](https://github.com/actions/setup-node)、[setup-uv](https://github.com/astral-sh/setup-uv)。维护时应连同 Node/Python 运行要求检查 SHA 更新。

本轮没有推送，**GitHub 托管 CI 尚未执行**。本机对应命令通过只能证明当前本机环境，不能提前给远程 CI 标绿；Linux 首次安装、网络、Docker镜像和浏览器系统依赖仍以实际 Actions 结果为准。既有更重的100用户合成查询及独立备份/删除重放演练继续保留在 `scripts/postgres/quality.py`、`backup_restore.py`、`lifecycle_restore.py`，不作为此次作品集上手的必要步骤。

历史重型演练以 S5-1/S5-2 的 Windows 实测为依据，`quality.py` 的服务子进程仍有 Windows venv 路径假设，不属于本轮 Linux CI 入口。当前新增跨平台快速启动/PG测试入口与 CI 使用的 HTTP、E2E 解释器路径已区分平台；其他平台仍须实际运行确认。
