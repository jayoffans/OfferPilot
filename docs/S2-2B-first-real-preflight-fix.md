# S2-2B-Fix：首次真实模型评测入口修复与免费验收

日期：2026-10-14（Asia/Shanghai）。基线 `feature/resume-agent` / `aad33bc`。保留上轮预检失败报告及全部有效实现；本轮没有读取真实 Key、调用 DeepSeek、推送、部署或修改实际开发/生产数据库，也没有进入 S3/Browser Agent。

**五个原阻塞已修复，免费零费用预检通过，技术上具备申请首次真实模型调用授权的条件。所有本轮实际 DeepSeek 请求/收费调用为0，费用0元。** Mock 测试结果，不代表 DeepSeek 真实模型质量。供应商 BYOK/训练退出/保留条件及生产发布阻塞没有解除。

## 1. 五个原阻塞的修复证据

| 原阻塞 | 修复及可复验依据 | 当前结果 |
|---|---|---|
| CLI绕过PDF | `pdf_evaluation.py`生成白名单PDF，写入实际文件后读回；经正式API上传、私有Storage、PDF parser、正文/页布局与parsed状态；下载逐字节核验 | PASS；2页/2081字节，迁移至0006，文件和正文hash都验证 |
| 没有最终发送预览 | 使用正式 `redact_resume`、版本与token上界生成完整预览；真实模式打印全文及hash，明确费用确认后还须输入完整发送SHA；经正式BYOK API取得授权快照再比对 | PASS；Spy解析真实 `generate_json` user_prompt中的JSON.resume_text，逐字及hash一致，变化时不调用 |
| 不进入待审核池 | 调用正式create/execute/claim/finish/ingest链，候选与成功状态在同一事务提交；不另建抽取/审核写入逻辑 | PASS；5个pending入临时SQLite，刷新/重登录保留，未确认四类正式值空，幂等无重复 |
| 无实际值/原文/定位和错误 | 报告保存允许公开的合成PDF提取全文、预览、候选值/分类/状态、字段raw_value、原文、页码与偏移、风险/错误案例及指标 | PASS；从已持久候选构造质量视图，不二次调用模型；具体expected/actual/证据回归覆盖 |
| Key输入不失败关闭 | `secure_key_input.py`要求原生stdin/stderr、fd0/2、TTY与OS控制台校验；GetPassWarning转异常，回退普通读取前拒绝 | PASS；无TTY子进程、重定向/替换流、隐藏失败、EOF/取消/非法Key均安全拒绝，无环境回退 |

Mock运行原来发送raw_text，本轮作必要最小修复，复用真实运行的正式脱敏函数。现有Mock Adapter仍只接受登记合成原文或其脱敏变体，未开放任意输入。Spy比对实际Provider参数后才调用委托；Key反射（含JSON Unicode转义）在进入Schema/候选数据库前拒绝，错误不携带Key或原响应。

## 2. PDF到审核的完整证据

可查看 [合成PDF](../backend/evaluations/fixtures/normal_chinese.synthetic.pdf) 与 [完整Mock报告](../backend/evaluations/s2-2b-pdf-mock-report.json)。这份PDF来自固定受信生成器，没有真实个人资料；包含明确虚构姓名、不可拨号号码和example.com域邮箱用于验证遮罩。模型只收到教育/技能/项目/实习的脱敏正文及固定Prompt，原件和这些直接联系资料不发送。

- PDF：`normal_chinese`，2页，2081字节，SHA256 `cd6e8940637829abaa911fd22689db0f43573a24328f50574300af2f36711567`。
- 临时SQLite经0001～0006实际迁移；独立合成邀请注册/登录、独立私有目录、正式上传/下载/文件完整性和parser。没有默认库或生产库连接，未修改迁移文件。
- 创建queued记录，执行领取和调用预算；Mock最多一次委托调用，真实请求0。V2/证据及pending与succeeded原子写入，报告从实际持久候选读回。
- 待审核5项，审核前教育/技能/项目/经历均空；重新登录能读回相同候选ID。显式Mock演示只确认Python、拒绝项目，之后技能1项，其余正式组仍空；拒绝候选没有正式条目。
- 完整中文工作台另验证：实际PDF上传/下载hash、页码/offset、成功任务重放、pending重登录恢复、确认Python、拒绝Java及再次重登录。截图见 [Mock审核隔离](../frontend/artifacts/screenshots/extraction-review-isolation-1440.png)，仅合成资料，Key为空。

| 实际Mock候选 | PDF原文证据示例 | 页码/结果 |
|---|---|---|
| 教育：示例大学、本科、计算机科学；2022-09入学、2026-06预计毕业 | `教育：示例大学；本科；计算机科学；2022年9月入学；预计2026年6月毕业。` | 第2页；pending |
| 技能：Python、JavaScript | `Python`、`JavaScript`（原文字段引用与字符偏移在JSON中） | 第2页；Python经显式演示确认 |
| 项目：课程选课系统、后端开发、2024-03～06 | 原文scope及技术栈/课程查询接口等字段片段 | 第2页；经显式演示拒绝，不进入画像 |
| 实习：示例科技、后端实习生、2025-07～09 | `维护测试脚本`、`补充接口测试用例` | 第2页；pending |

正常样本非空正式字段Precision/Recall为 **19/19（100%）**，字段证据定位 **21/21（100%）**；严重错误0。字段和证据数量不同，不能混作分母。报告明确Mock、token为null（未测量），协议委托1次，真实/收费0次，实际成本0元；这些结果不代表真实模型正确率。

## 3. 安全输入与真实授权边界

Key没有命令行参数或环境回退。CLI在费用/完整hash确认后才调用安全输入；输入后通过现有Web BYOK API和SessionKeyVault管理，不创建持久Key库。原生控制台条件不满足时拒绝继续，不能把本工具的非交互管道用作真实Key入口。

真实入口只允许 `normal_chinese`、`--max-calls 1`、输出1～4096 token、执行当天核查的峰值单价、正数预算、负责人授权记录和全新报告账本；`--demonstrate-review`在真实模式禁止。实际请求更低的输出上限时报告以对应请求上限估算，API预览4096仍为最大授权边界。设置flag不代替负责人之后的明确收费授权。

调用前既有DB账本加计数/unknown，评测账本fsync预占；Spy的输入核验和账本写入通过后才委托Provider。失败/超时/连接中断不补发，未知费用保持未知；真实retry、批量/其它样本和自动审核拒绝。已存在报告不能覆盖或重放。

每份样本使用独立临时账户/DB/存储，退出关闭客户端和数据库、销毁Key与测试原件；可重新登录验证持久性发生在该隔离会话内。报告保留公开合成值供审查，CLI退出后不会保留一个长期用户账户或待审核库。产品Web与正式账户的数据保留/导出删除仍按既有后续范围，不因评测临时清理宣称生产能力完成。

## 4. 免费执行入口

仓库根目录：

```powershell
# 实际生成/读回/上传合成PDF，展示最后的脱敏预览；0 Provider、0 Key。
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py
# 单样本Spy/Mock、真实持久候选及显式测试审核；0外网/付费。
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py `
  --mode mock --case-id normal_chinese --demonstrate-review
# 需落报告时使用全新 --output 路径，不能覆盖已存在账本。
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

未来真实命令和独立授权条件见 [执行手册](S2-2B-real-evaluation.md)。本轮未执行真实命令，也没有请求收费授权。查看报告/PDF、运行免费模式或设置任何开关都不构成真实调用授权。

## 5. 修改文件及原因

| 文件 | 原因 |
|---|---|
| backend/evaluations/pdf_evaluation.py | 隔离临时环境、合成PDF、正式API/运行/审核与报告桥接；不选择外网Provider |
| backend/evaluations/evaluate_deepseek_v2.py | 去掉文本直抽入口；接入PDF桥接、全文/hash确认、具体错误/实际候选报告、单样本收费门槛 |
| backend/evaluations/secure_key_input.py | 隐藏输入验证与失败关闭，安全异常，不改Vault |
| agent_runtime/extraction_runs.py、providers/mock.py | Mock和真实模式共用脱敏边界，继续仅匹配登记合成材料 |
| tests/test_pdf_evaluation_pipeline.py、test_secure_key_input.py | 20项PDF业务集成/外网禁止、14项输入安全回归 |
| tests/test_deepseek_evaluation_quality.py、test_deepseek_paid_boundaries.py | 原质量/费用护栏迁至真实PDF业务路径，增加完整预览/TTY/严格单样本/显式审核；不改gold掩盖错误 |
| frontend/e2e/extraction.spec.ts | 加强既有真实Mock浏览器闭环，无新特权端点或业务UI |
| fixtures/normal_chinese.synthetic.pdf、s2-2b-pdf-mock-report.json | 可供人工检查的公开合成证据；非真实模型结果 |
| README、PLAN、当前执行手册/验收文档 | 同步范围、命令、结果和未解除的阻塞，保留上轮失败报告 |

## 6. 免费复验与新增问题处理

| 最终验证入口 | 实际结果 |
|---|---|
| 后端全量 unittest discover，development / 内存默认DB、测试各自临时库 | **324/324**，108.279秒，失败0/跳过0；保留原286，新增38 |
| PDF集成专项、安全输入、CLI质量/收费边界 | **20/20、14/14、26+6**，纳入全量；含实际PDF和真实业务失败/权限围栏 |
| 既有BYOK生命周期/隔离 | **15/15**专项及全量均通过 |
| 前端Vitest | **49/49**，13.70秒，失败0/跳过0 |
| 完整Chromium | **18/18**，约1.3分钟，失败0/跳过0/retries0；加强已有Mock流程 |
| 契约、typecheck、lint、build | 通过；首次E2E TS类型错误修复后构建通过 |
| 真实localhost HTTP生命周期smoke | exit0，服务/临时目录正常清理 |
| 实际单PDF Mock CLI、显式审核报告 | exit0，字段19/19、定位21/21；候选5、正式审核前4组空；0真实调用，usage未知 |
| 默认plan与零费用重新预检 | exit0，实际PDF parsed、相同字节hash及最终预览；断言Provider/安全Key函数未调用；联系信息遮罩，五项阻塞关闭 |
| 差异/旧迁移/语法/秘密/清理 | diff检查通过，0001～0006无改动；90文件AST和项目/锁TOML通过；变更未发现Key形态或秘密配置；E2E监听与本轮PDF临时目录0 |

所有模型调用均为明确Spy/Mock或协议桩，外网模型请求0。原有测试没有删减，CLI测试适配新PDF入口后仍验证相同质量/费用护栏；Mock token不编造。收尾检查已完成，没有新增依赖或迁移；本地保存修复检查点，不推送。

真实发现和处理如实记录：CLI整合时质量字典中的候选数量字段覆盖了候选数组，修为独立candidate_count并由CLI集成回归验证；首轮Chromium17通过/1失败是独立PDF oracle漏计PyMuPDF保留的段间空行，经实际parser复核修正oracle后18通过，未放松hash/offset断言；首次build发现E2E可选locations的TS类型错误，经严格非空断言和narrowing修复后typecheck/build通过。Starlette TestClient/httpx弃用提示仍在，本轮没有为消提示升级依赖。

## 7. 重新零费用预检与判断

五个原阻塞已逐项解决：实际PDF输入、最终脱敏Provider Spy一致性、真实pending提交/重登录/人工审核隔离、具体合成候选与证据/错漏报告、安全Key输入。已明确测试预览/源码变化、PDF完整性错误、跨用户、重复运行、删除/取消迟到、事务故障、Key回显和不可隐藏输入等失败路径，不以Mock成功代替错误/权限边界。

**最终回答：是，当前已具备申请首次真实 DeepSeek 单样本评测授权的技术条件。** 仅限负责人之后明确批准次数与预算的一份纯合成、本机隔离评测；不是已经获得付费授权，也不是允许公开部署。本轮完成免费修复后停止，不自行调用真实模型。真实DeepSeek网络/质量/费用仍未验收；第三方BYOK许可、API训练退出与确切保留、真实个人资料脱敏长尾、单进程/部署安全、账户完整导出注销与备份删除仍为发布阻塞。
