# OfferPilot 本地演示路线

日期：2026-10-18。所有账户、学校、公司、简历、岗位与申请均为合成信息；截图来自真实 Next.js + FastAPI 页面。**候选演示明确使用无网络 Mock，不是 DeepSeek 输出，不代表真实模型准确率。** 本轮不输入真实 Key，不向模型发送请求。

## 1. 准备

按 [LOCAL_SETUP](LOCAL_SETUP.md) 安装锁定依赖、初始化独立数据库和合成种子，再启动 API 与 Web。默认关闭真实模型；无 Key 也能演示手工画像、JD、匹配、投递与导出。合成 PDF 和随机演示凭据仅在忽略的 `.local/` 中生成；不要上传自己的真实简历或替换为真实申请记录。

已有 E2E 可在不保留登录环境的情况下完整重现页面操作，截图流程见第 4 节。下面图片来自独立 E2E 场景，不假设所有图片属于同一账户或同一条申请。随机 `e2e-…@example.com` 是测试身份，不是真实联系方式。

## 2. 建议现场路线（约 8～10 分钟）

| 顺序 | 页面/动作 | 讲解重点 |
|---|---|---|
| 1 | `/login` 登录 → `/` 工作台 | 会话登录、行动分组、收藏不算申请；今日日期按北京时间 |
| 2 | 账户菜单 → 简历管理 → 简历详情 | PDF 验证、文本提取、本人下载；上传不触发 AI |
| 3 | `/profile` 正式画像 | 可手工完成，未知留空；用户填写与候选审核来源分开 |
| 4 | 单独展示合成 Mock 候选 → 展开证据 | 先声明 Mock；定位由服务器重验，否定/计划不能原值确认，pending 不参与匹配 |
| 5 | `/jobs` → 添加职位 → 粘贴合成 JD → 免费规则整理 | 全部先待审核，必须/优先/待核实分开；保存链接不抓取网页 |
| 6 | 确认适用条件 → 保存 → 匹配 → 展开证据 | 纯规则四态，无证据不等于不具备；双侧依据、没有录用概率 |
| 7 | 修改正式资料 → 回职位看旧匹配失效 | revision 与版本快照，主动重算而非静默改历史 |
| 8 | 准备投递 → 修改状态与日期行动 → 工作台完成 | 人工记录实际投递，未知时刻保持空；时间线追加、幂等与冲突 |
| 9 | 账户菜单 → 账户设置 → 密码重验 → 导出 ZIP | 白名单导出、私有鉴权下载、manifest 及遗漏说明 |

注销会清空演示账户，不作为普通演示必点步骤；可讲解 S5-2 的真实清理测试与主库外删除清单。现场需要重复体验时使用新的隔离演示环境，不尝试用旧数据库备份绕过删除清单。

## 3. 截图清单

### 登录与工作台

![未填写凭据的登录页](images/demo/login.png)

![今日行动与当前申请统计](images/demo/workbench.png)

### 简历管理

![合成简历详情，文本解析与 AI 抽取状态分开](images/demo/resumes.png)

### AI 候选与证据审核（Mock）

![Mock 合成候选与原文定位；Java/Go 反例保留待核实](images/demo/candidates-mock.png)

图片来自登记合成 PDF 的 Mock Adapter。真实 DeepSeek 只可引用注明日期的[历史单样本报告](S2-2B-single-real-evaluation-2026-10-14.md)，本轮没有重新调用模型，不能把截图当作其真实返回。

### 正式画像

![手工保存的合成画像](images/demo/profile.png)

### 职位与 JD 审核

![合成职位列表](images/demo/jobs.png)

![免费规则整理后的条件经用户审核](images/demo/jd-review.png)

### 匹配分析

![确定性匹配的四态及双侧证据](images/demo/matching.png)

### 投递管理

![申请状态、下一步与追加时间线](images/demo/application.png)

### 本人数据导出

![导出就绪与已清空的密码框](images/demo/export.png)

### 移动端

![390px 工作台](images/demo/workbench-mobile.png)

## 4. 重新生成与核对

在仓库根目录执行（Windows 使用 `npm.cmd`）：

```sh
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
uv run --locked --project backend python scripts/collect_demo.py
uv run --locked --project backend python scripts/collect_demo.py --check
```

运行全套 E2E 会新建临时库、合成账户和文件，启动本机 8001/3001 的真实 API/Web，结束清理；端口占用则拒绝复用已有服务。测试中只有模型/故障注入使用免费夹具，页面、认证、业务持久化、匹配与导出均走真实代码。不要与 build 或另一个 E2E 进程并发写 `.next`。

收录脚本只复制固定的 11 张 PNG，不修改图片，也不复制登录输入、Key 对话框或 ZIP。来源路径、对应测试、宽高、大小与 SHA-256 见 [manifest.json](images/demo/manifest.json)。本批已逐张检查合成身份、空密码框、Mock 标签和布局；以后重生成仍须人工复核再提交。脚本的哈希检查只能证明文件一致，不能代替隐私审查。

原始运行截图仍留在忽略的 `frontend/artifacts/`；公开内容仅为本页固定清单。保留全页图片，未用生成图或 UI 改图包装功能。
