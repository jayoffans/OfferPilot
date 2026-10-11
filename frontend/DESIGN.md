# S1-3 中文工作台设计与前端约定

本批交付账户与 PDF 文件管理。首个任务是“上传我的第一份简历”。画像确认、职位管理和投递进度仍属后续，不创建空导航或虚假指标。

## 信息架构

| 路由 | 主任务 | 状态与边界 |
|---|---|---|
| `/register` | 使用邮箱绑定的邀请码注册 | 密码 12～128 字符；注册成功后去登录，不自动登录 |
| `/login` | 登录与恢复会话 | 密码显隐、字段错误、提交反馈；已登录进入工作台 |
| `/` | 从首次上传开始、找到最近文件 | 全部数字来自本人真实列表；只显示保存数量和最近上传时间 |
| `/resumes` | 上传、搜索、排序、分页找文件 | 每页 8 份，空结果与空账户分别引导；刷新重新读服务器 |
| `/resumes/[id]` | 改名、原件下载、确认删除、查询清理状态 | 元数据和生命周期分别请求；隐藏/失败文件仍可凭此 URL 查询并重试删除 |

桌面为 224px 侧栏与账户顶栏；800px 以下改为顶部品牌、账户菜单及两个直接导航入口。主内容最大宽度 1320px，移动端保留 18px 边距。列表移动端将状态放到第二行，文件名截断但详情显示完整名称。

## 设计 Token

| 项目 | 值与用途 |
|---|---|
| 品牌色 | `#4258d6`，主操作、当前导航、链接 |
| 主文字 | `#18263b` |
| 次文字 | `#647185` / `#6b788d`，说明与次信息 |
| 画布 / 面板 | `#f6f7f9` / 白色 |
| 边界 | `#e5e9f0` |
| 圆角 | 输入 7px、按钮 8px、面板 10px、弹窗 13px |
| 字体 | Segoe UI、PingFang SC、Microsoft YaHei、系统 sans；无需远程字体 |
| 字号 | 页面标题 26～30px、区块 16～18px、正文 12～14px、次信息 10～11px |
| 焦点 | 3px 可见焦点圈；尊重 `prefers-reduced-motion` |

图标来自 Lucide；避免外部图片、装饰图表、玻璃拟态与假业务数据。空状态明确文件类型、大小、页数及下一步。

## 交互规范

- 认证由 FastAPI 的 HttpOnly、SameSite Cookie 管理。浏览器不读 Cookie，不把用户凭据写入 localStorage/sessionStorage；CSRF 仅内存保留，刷新后从真实接口恢复。
- 上传需明确选择文件并点击提交。进度来自 XHR 实际传输事件；100% 仅表示请求已发送，保存和文本解析须等 201。超时、网络中断或未知结果要求先检查列表；有 Location 时直接查询服务器生命周期。
- 改名和删除使用 Radix Dialog；改名打开聚焦输入，关闭恢复原按钮。删除确认优先保留文件，不因 503 提示已删除；完成 204 后显示已删除生命周期记录与返回列表入口。
- 普通列表 API 只返回 active+parsed；不虚构“处理中”文件。处理中的详情最多自动查询 10 次，每次间隔 2 秒，之后用户手动刷新；离开页面终止请求和计时器。
- 401 返回登录；404 统一不可访问说明；409 要求刷新/核查；429 不自动重试；503 提供真实状态和清理恢复。下载 blob 在使用后撤销引用。
- 文件名始终以 React 文本渲染，不使用 `dangerouslySetInnerHTML`。SQLite 无时区日期按 UTC 解析，以 Asia/Shanghai 展示。

## 组件与依赖

`src/app` 负责路由、元数据、中文根布局和安全代理入口；`src/components` 负责账户、导航、上传、列表与详情；`src/lib` 负责生成类型、API/CSRF、认证上下文与代理。UI 无数据库访问与业务授权逻辑，后端仍是唯一权威。

| 依赖 | 解决的问题与运行成本 |
|---|---|
| Next.js 16 / React 19 | App Router 和同源 Route Handler；需本机 Node 进程；使用官方维护包 |
| TypeScript 5.9 / OpenAPI TypeScript | 严格类型与从真实 Schema 生成契约；开发期成本，不手写重复业务 Schema |
| Tailwind CSS 4 | 基础样式编译与统一 Token；构建期依赖，最终输出 CSS |
| Radix Dialog / Dropdown | 键盘、焦点与弹窗/账户菜单基础；小范围客户端代码 |
| Lucide React | 同一套图标，可按导入裁剪 |
| Vitest / Testing Library / jsdom | 核心表单与网络边界测试，开发依赖 |
| Playwright | 独立 Chromium 与真实 HTTP E2E；浏览器只用于本地测试 |
| ESLint 10 | 当前维护版本，开发依赖；Next 的部分传递插件仍声明仅支持至 9，安装有 peer 告警，实际完整 lint 已验证 |

所有直接依赖固定版本，传递依赖由 `package-lock.json` 固定。没有新增全局状态库、通用 Agent 框架、云服务、后台队列或付费模型调用。依赖具体版本见 package.json，不以最新大版本代替兼容性验证。

实现遵循随已安装 Next 包提供的 Route Handler / Server and Client Components / cookies 文档；公开参考为 [Next.js 安装](https://nextjs.org/docs/app/getting-started/installation) 与 [Tailwind Next.js 集成](https://tailwindcss.com/docs/installation/framework-guides/nextjs)。

ESLint 9 已停止维护（[官方支持周期](https://eslint.org/version-support/)），因此保留 ESLint 10 而不为消除安装告警退回已终止维护版本。Next 传递的 import/react/jsx-a11y 插件 peer 元数据更新列为开发工具技术债；不影响本批 lint 命令实际运行结果。
