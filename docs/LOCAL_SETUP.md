# 本地运行

本项目以 GitHub 作品展示和本机技术演示为目标。推荐 PostgreSQL + 本机 FastAPI / Next.js；不需要云服务器、域名或 DeepSeek Key。Docker 只运行已有的 PostgreSQL 服务，避免把 Python、Node 开发调试再包进多层容器。

## Windows 一键演示

先安装 Python 3.11、uv 和受支持的 Node.js / npm（版本见下节），再双击仓库根目录的 [start-demo.cmd](../start-demo.cmd)，或在 PowerShell 执行：

```powershell
.\start-demo.cmd
```

入口只编排现有 `manage.py`：按锁文件执行 `uv sync` 和 `npm ci`，在独立 `.local/windows-demo/` 初始化 SQLite、迁移并播种合成数据，再启动单进程 FastAPI 和 Next.js，健康检查就绪后打开 `http://127.0.0.1:3000`。**这条路线不需要 Docker 或模型 Key，只使用明确标识的 Mock。** 安装需要可访问依赖源；5 High 和 ESLint peer 警告仍会显示，不用强制升级或忽略参数掩盖。

默认 profile 与下文旧 `demo`、PostgreSQL 环境及 `backend/data/` 分开，不复制或修改已有 `.env`。后端演示子进程不载入开发者的 `.env`，前端现有服务配置由本机地址显式覆盖。若已有播种记录，不会重播种或还原之后的用户操作；密码仍由本人打开提示路径中的私有 `demo-credentials.json` 查看，程序不打印密码。

8000 / 3000 端口被占用或 Next 开发锁存在时会拒绝启动，不复用、终止其他服务，也不偷偷换端口。按 Ctrl+C 停止本次启动的 API / Web 及其子进程；若 Windows 询问 `Terminate batch job (Y/N)?`，输入 `Y` 关闭批处理，数据库保留。安装、初始化或就绪检查失败时同样清理本次服务并保留现场；不要删库或重建删除清单来绕过错误。

需要新的独立演示或不自动打开浏览器时：

```powershell
.\start-demo.cmd --profile interview-demo --no-browser
# 只有依赖已按锁安装、且锁文件未变化时才跳过安装。
.\start-demo.cmd --profile interview-demo --skip-install --no-browser
```

默认每个服务的就绪等待上限为 90 秒，可用 `--startup-timeout 120` 调整（允许 10～180 秒）。现有跨平台 `init` / `seed` / `api` / `web` 命令保持，见下文；已有 profile 的后续迁移仍须停写和备份后显式执行 `migrate`，一键入口不隐式升级已有库。

## 1. 准备依赖

- Python 3.11（后端声明 `>=3.11,<3.12`）。
- uv；本轮本机验证版本为 0.12.19。
- Node.js 22.16.0 和 npm；项目允许的其他版本范围见 `frontend/package.json`。
- 使用 PostgreSQL 时安装并启动 Docker Desktop / Docker Engine 和 Compose v2+。Windows Docker Desktop 使用 Linux 容器。

在仓库根目录执行，Windows PowerShell 中如命中 npm 执行策略，可将 `npm` 写作 `npm.cmd`：

```sh
uv sync --project backend --locked
npm --prefix frontend ci
```

不需要复制或修改已有 `.env`。下面的本地工具显式指定自己的数据库、原件、导出、删除清单和服务地址，不会迁移 `backend/data/` 下的既有开发库。需要查看所有配置项时，参考 `backend/.env.example` 和 `frontend/.env.example`，示例不能包含真实 Key。

## 2. 初始化和合成数据

推荐 PostgreSQL：

```sh
uv run --project backend --locked python scripts/local/manage.py init --database postgres
uv run --project backend --locked python scripts/local/manage.py seed
uv run --project backend --locked python scripts/local/manage.py status
```

`init` 使用已固定版本和镜像摘要的 `compose.postgres.yml`，仅监听 `127.0.0.1:55432`，健康等待最多90秒。首次生成忽略的 `.env.s5-postgres` 随机凭据；已有文件和 Docker 卷不覆盖、不重置。随后创建全新随机命名的 `offerpilot_portfolio_*` 库，由 `s5_migrator` 向前迁移至 Alembic head（当前 `0010_account_lifecycle`），给 `s5_app` 必要 DML 权限，并初始化这套新环境的独立删除清单。应用始终使用 `s5_app`，不以超级用户或迁移角色运行。

不想安装 Docker 时，可用 SQLite 体验相同产品流程：

```sh
uv run --project backend --locked python scripts/local/manage.py init --database sqlite
uv run --project backend --locked python scripts/local/manage.py seed
```

两种方式都使用默认 `demo` 环境，不能在已有环境上直接切换数据库。需要同时保留两套环境时，给每个命令加同一个 `--profile`，位置在子命令前：

```sh
uv run --project backend --locked python scripts/local/manage.py --profile sqlite-demo init --database sqlite
uv run --project backend --locked python scripts/local/manage.py --profile sqlite-demo seed
uv run --project backend --locked python scripts/local/manage.py --profile sqlite-demo api
```

私有状态保存在仓库内忽略的 `.local/<profile>/`，包括数据库连接配置、随机生成的演示账户密码、PDF 原件、导出文件和独立删除清单。默认演示邮箱为 `portfolio-demo@example.com`；密码请在本机编辑器中查看 `status` 提示的 `demo-credentials.json`，不要截图、提交或分享这个文件。脚本不会在输出中打印密码。

`seed` 通过真实 FastAPI 用例/API 建立一个合成账户、手工正式画像、三个职位、已审核的合成 JD 条件、确定性匹配及三条申请，下一步分别为播种当天、前一天和三天后。另上传固定合成 PDF，运行无网络 Mock Provider 生成待审核候选。所有 Mock 来源保留“测试”标识，候选不自动确认，正式画像的手工数据与候选分开。重复 `seed` 不增加记录；`status` 会检查初始演示数据和候选状态。用户实际审核或删除种子后，初始状态检查可能不再通过，已保存的用户操作不会被还原。

本地工具强制关闭真实 DeepSeek，仅开放现有隔离 Mock 演示。没有 Key 也能使用手工画像、PDF 管理、岗位规则整理、匹配、投递、导出和注销。Mock 展示的是流程和证据校验机制，不代表真实 DeepSeek 抽取质量。

## 3. 启动两个服务

终端 A（仓库根目录）：

```sh
uv run --project backend --locked python scripts/local/manage.py api
```

终端 B（同一仓库根目录）：

```sh
uv run --project backend --locked python scripts/local/manage.py web
```

打开 [工作台](http://127.0.0.1:3000)，用刚生成的合成账户登录。API 健康检查为 [health](http://127.0.0.1:8000/health)，接口文档为 [OpenAPI](http://127.0.0.1:8000/docs)。浏览器请求由 Next 同源代理转发到 FastAPI；演示服务只监听本机，API 为单进程。演示路线见 [DEMO](DEMO.md)。

`seed` 在独立本地进程中使用正式 FastAPI app / TestClient，并不要求启动 HTTP 服务；建议先完成播种再启动 API，避免与后台任务进程争用。同一环境只运行一个 API 进程；不要同时用开发服务、E2E 或构建过程写同一个 `frontend/.next`。

两个终端分别按 Ctrl+C 停止。数据保留以便下次继续，数据库可单独暂停：

```sh
docker compose --env-file .env.s5-postgres -f compose.postgres.yml stop
```

下次继续使用已有 PostgreSQL 环境时，先启动 Docker，再恢复数据库，随后使用同一 `--profile` 启动 API/Web：

```sh
docker compose --env-file .env.s5-postgres -f compose.postgres.yml up -d --wait
```

已完成初始化的 `init` 只保留配置，不会重建数据库或替你恢复已停止的容器；不要删除密码文件或卷来处理连接失败。

已有环境后续更新迁移时，先停止写入并备份选定的合成数据库、原件和独立清单，再执行：

```sh
uv run --project backend --locked python scripts/local/manage.py migrate
```

重复 `init` 保留环境，不替你升级已有库；`migrate` 才是显式向前升级入口。工具没有删除环境或重置 Docker 卷的命令。需要重新演示时选择新的 `--profile`；不要在丢失删除清单的旧环境上初始化空清单以绕过恢复。

## 4. 可选的手动运行

现有开发者可继续自行管理环境变量，不必迁移到上述工具。以下为一套**全新的 SQLite 合成环境**，不覆盖 `.env`。PostgreSQL 手动配置时仍应区分迁移/运行角色，运行角色权限要求见 [S5-1 验收](S5-1-acceptance.md)。

PowerShell，终端 A：

```powershell
$demoRoot = Join-Path (Get-Location) '.local/manual-demo'
if (Test-Path -LiteralPath $demoRoot) { throw '请为全新演示环境选择未使用的目录' }
New-Item -ItemType Directory -Path $demoRoot | Out-Null
$env:OFFERPILOT_ENVIRONMENT = 'development'
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///' + (Join-Path $demoRoot 'demo.db').Replace('\', '/')
$env:OFFERPILOT_UPLOAD_DIR = Join-Path $demoRoot 'uploads'
$env:OFFERPILOT_EXPORT_DIR = Join-Path $demoRoot 'exports'
$env:OFFERPILOT_DELETION_LEDGER_DIR = Join-Path $demoRoot 'deletion-ledger'
$env:OFFERPILOT_TRUSTED_ORIGINS = '["http://127.0.0.1:3000"]'
$env:OFFERPILOT_SESSION_COOKIE_SECURE = 'false'
$env:OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED = 'false'
uv run --project backend --locked python -m alembic -c backend/alembic.ini upgrade head
# 仅限刚创建的全新环境；已有/恢复环境必须保留并重放原清单。
uv run --project backend --locked python -m offerpilot_api.account_lifecycle.recover --initialize-ledger
uv run --project backend --locked python -m offerpilot_api.identity.invites --email manual-demo@example.com
uv run --project backend --locked python -m uvicorn offerpilot_api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

手动方式只签发一次本机邀请，不生成种子；使用该邮箱、邀请码和自设密码在页面注册。邀请码只显示一次，不要写入报告。

删除清单必须在启动 HTTP 服务前准备完成，注册请求不会自动创建清单，即使用户表为空也一样。标记通过同一文件系统的硬链接发布，适用于已验证的 Windows NTFS 与 Linux 文件系统；不支持硬链接、权限不足、标记缺失或损坏时停止并拒绝访问，不能通过重建既有/恢复环境的清单解除故障。准备进程中断可能留下父目录中的 `.registry-init-*` 临时目录；它不参与删除重放，须由可信维护者停写核对后清理。

终端 B：

```powershell
$env:OFFERPILOT_API_URL = 'http://127.0.0.1:8000'
$env:OFFERPILOT_WEB_ORIGINS = '["http://127.0.0.1:3000"]'
$env:NEXT_TELEMETRY_DISABLED = '1'
npm.cmd --prefix frontend run dev
```

macOS / Linux 可用同名环境变量的 `export NAME=value` 语法；推荐直接使用跨平台 `manage.py`，以免复制时遗漏私有目录、可信 Origin 或数据库身份。

## 5. 排查

| 现象 | 处理 |
|---|---|
| Docker 未运行、镜像下载失败 | 启动 Docker，确认 Linux 容器和网络，再重跑同一个 `init`。不要更换已有卷密码或移除卷 |
| 55432 被占用或位于 Windows 保留端口范围 | 初始化前在私有 `.env.s5-postgres` 设置空闲的 `S5_POSTGRES_PORT`，可用 `netsh interface ipv4 show excludedportrange protocol=tcp` 查看保留范围；已有环境连接配置不能自动迁移到另一个实例 |
| 8000 / 3000 被占用 | 停止自己启动的旧 API / Next 服务；不要用其他项目服务假装启动成功 |
| 种子中断 | 失败环境保留；选择新的 `--profile` 重试。不要重放密码未知或已修改的旧种子 |
| 登录后仍是登录页 | 使用同一 `127.0.0.1` 地址，确认密码来自当前 profile；工具会为本机 HTTP 固定关闭 Secure Cookie |
| 403 / Origin 错误 | 确认前后端来自同一套工具；手动运行时同时配置双方可信来源，不跳过 CSRF |
| 删除清单缺失或损坏 | 失败关闭是设计行为。保留现状，参照 S5-2 恢复流程；不要初始化空清单来恢复访问 |
| npm 请求失效代理 | 检查当前终端的代理配置，按自己的网络情况处理；不关闭 TLS 校验 |
| TestClient deprecation warning | 当前锁文件中的 Starlette 提示未来迁移 httpx2；本轮不改依赖，API、种子和测试仍需以实际返回结果判断 |

## 6. 本轮实测口径

2026-10-18，Windows 11 / Python 3.11.5 / uv 0.12.19 / Node 22.16.0 / Docker Desktop / PostgreSQL 18.6：全新 SQLite 和全新 PostgreSQL 环境的初始化、0010 迁移、合成播种、重复播种及初始状态检查通过；PostgreSQL 运行角色权限检查通过。真实 loopback API 启动、health、登录，以及 `manage.py web` 启动、Next 同源代理登录和工作台三条合成申请均通过；自建 API / Web 进程已停止，合成环境保留。完整测试入口及 CI 边界见 [TESTING](TESTING.md)。2026-10-20，合并后的 main 已通过 Ubuntu 24.04 上的 [GitHub Actions](https://github.com/jayoffans/OfferPilot/actions/runs/39386760528)，包括 SQLite / PostgreSQL 合成环境复现和 Chromium E2E；macOS 尚无实际验证记录。
