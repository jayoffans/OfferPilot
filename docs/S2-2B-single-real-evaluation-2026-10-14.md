# S2-2B：首次真实 DeepSeek 单样本质量评测报告

日期：2026-10-14（Asia/Shanghai）。仅限用户本轮明确授权的 `normal_chinese.synthetic.pdf`；所有结果来自唯一一次真实 DeepSeek 返回及正式业务候选池，不使用 Mock 替代。

**本次正常合成样本验收通过：真实请求累计 1 次，无自动重试；5 个候选全部 pending，正式画像四组均为空。19/19 非空字段正确，21/21 证据定位正确，19/19 非技能范围归属正确，错误和遗漏均为 0。输入 1107、输出 781、总计 1888 tokens；峰值价格估算 0.008462 元，实际账单金额尚未确认。**

此结论仅适用于本次正常单样本。本轮已经停止，不运行其余四份、不进入 S3、不推送 GitHub、不部署公网；否定/计划和同类多经历能力未验收。

## 1. 授权、发送前复检与请求参数

原授权及后续继续指示共享最多一次真实请求、输出最多 4096 tokens、预算人民币 1 元。本轮只使用完全虚构的固定 PDF，不读取真实个人简历、不调用其他收费服务。

| 项目 | 实际记录 |
|---|---|
| 指定文件 | `backend/evaluations/fixtures/normal_chinese.synthetic.pdf`；2081 字节、2 页 |
| PDF SHA256 | `cd6e8940637829abaa911fd22689db0f43573a24328f50574300af2f36711567` |
| 输入同一性 | 既有 CLI 的临时 PDF 与指定文件逐字节相同；上传/parser/私有下载字节回读通过 |
| 提取正文 SHA256 | `7dd69e69b7e12693b837cb32b312fc65d6e115bf5a37737bd2e9485439962142` |
| 最终发送 SHA256 | `1b4bb1d1f681092c0de6946913588d45c30bf543ed73994d0c40b64b2e025b0d` |
| 固定 Prompt | `resume_extract_v2.1`；SHA256 `a4cdd80c216976470829dc2d109cfa129709a5a844b3484b0a61eb63e7456d87` |
| 版本 | Schema 2.0；`resume-redaction-v1`；SDK openai 2.54.0；`deepseek-openai-json-v1.1` |
| 预检一致性 | PDF、脱敏全文、source_version、Prompt 与已通过的零费用预检一致；续跑再次复核一致 |
| 输入保守上界 | 16548 tokens（JSON UTF-8 字节数加固定 Prompt/包装余量，不是真实用量） |
| 请求模型 | `deepseek-flash`；响应模型别名亦为 `deepseek-flash` |
| 模式 | `thinking.type=disabled`、`response_format.type=json_object`、`stream=false` |
| 输出/调用 | `max_tokens=4096`；max_calls=max_attempts=1 |
| 传输 | 官方 `https://api.deepseek.com/chat/completions`；SDK/HTTP 重试均 0，不跟随重定向，不读取环境代理，保留 TLS 验证 |

2026-10-14 核查 [DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：以 flash 峰值未缓存输入 2 元/百万 tokens、输出 8 元/百万 tokens计算，不假定缓存命中或空闲折扣。发送前上界 `(16548×2 + 4096×8)/1000000 = 0.065864 元`，低于 1 元预算。官方当日模型别名对应 DeepSeek-V4.1-Flash；响应别名不证明不可变的权重版本。

实际 Provider 的 JSON.resume_text 经 Spy 与预览逐字及 hash 比对成功；完整请求另含固定版本 Prompt 与 JSON 包装。PDF 原件和合成联系资料没有发送。最终脱敏正文：

```text
*******************
********
***************************************************
***********
教育：示例大学；本科；计算机科学；2022年9月入学；预计毕业：2026年6月。
技能：Python。
技能：JavaScript。
项目：课程选课系统；角色：后端开发；2024年3月至2024年6月；技术栈：Python；工作：编写课程查询接口。
实习：示例科技；岗位：后端实习生；2025年7月至2025年9月；职责：维护测试脚本；成果：补充接口测试用例。
```

## 2. 单次真实运行、候选池与画像边界

复用隔离临时 SQLite（迁移至 `0006_deepseek_byok`）、真实邀请身份/API、私有 Storage、正式 parser、BYOK/Vault、create/claim/execute/finish/ingest。模型结果经 V2 和证据验证后与 succeeded 状态原子提交到现有 profile_candidates 业务池，没有另建事实导入逻辑。

| 项目 | 实际结果 |
|---|---|
| run_id | `576363be-26d1-42f0-8ac2-81a2ccc7b476` |
| 状态 | run=`succeeded`，评测=`validated`，Schema=`passed` |
| 累计调用 | 所有七份启动/输入账本合计 actual_calls=paid_calls=reserved_calls=1；只有最终弹窗账本为 1，其余均 0 |
| 单次边界 | Provider invocation=1、run.call_count=1、attempt_count=1；无修复调用、自动重试或补发 |
| 计量状态 | `usage_reported`；输入1107、输出781、合计1888 |
| 候选 | 5/5 pending、5/5 supported，entry_id 与 reviewed_at 均 null；supported 仅表示证据护栏通过 |
| 正式画像 | education=[]、skills=[]、projects=[]、experiences=[]；unconfirmed_candidates_not_in_profile=true |
| 重登录恢复 | 候选 ID 集合一致，relogin_restores_candidate_ids=true |
| 发送核对 | preview_matches_actual_provider_input=true；actual_sent_sha256 与批准值相同 |

数据库运行创建时间为 UTC 09:25:19，完成为 09:25:24.430423，即北京时间 17:25:19～17:25:24.430423。约 5.43 秒是运行创建至完成的间隔，包含本地处理，不能当作独立 HTTP 时延。账本未单独保存成功 HTTP 状态码或供应商请求 ID，不虚构这些信息。

本轮没有调用任何候选确认/修改/拒绝接口。最终成功运行退出后会话 Key、临时账户、私有原件及临时数据库由既有隔离生命周期清理；实际入池状态与合成候选/证据保留在 JSON 报告。这里验证的是隔离环境中的现有业务待审核池，不是长期正式账号中的待审核数据。早期强制关闭输入进程留下的目录另见第7节收尾记录。

## 3. 实际候选值与逐字段核对

候选身份按独立 gold 一对一匹配。下面各实际值与 gold 严格一致，描述也完全一致，没有“等价改写被计错”的情况。所有字段证据位于 PDF 第 2 页；完整候选 UUID、raw_value、归一值、断言、页段和风险信息见原始 JSON。

| 候选 | 实际字段 | 实际值 | gold 核对 |
|---|---|---|---|
| 教育 | school | 示例大学 | 一致 |
| 教育 | degree | 本科 | 一致 |
| 教育 | major | 计算机科学 | 一致 |
| 教育 | start_date | 2022-09 | 一致 |
| 教育 | end_date | 2026-06 | 一致 |
| 教育 | end_date_kind | expected | 一致 |
| 技能 | name | Python | 一致 |
| 技能 | level | null（未知） | 未知保持正确 |
| 技能 | name | JavaScript | 一致 |
| 技能 | level | null（未知） | 未知保持正确 |
| 项目 | name | 课程选课系统 | 一致 |
| 项目 | role | 后端开发 | 一致 |
| 项目 | description | 技术栈：Python；工作：编写课程查询接口 | 一致 |
| 项目 | start_date | 2024-03 | 一致 |
| 项目 | end_date | 2024-06 | 一致 |
| 项目 | is_current | null（未知） | 未知保持正确 |
| 实习 | organization | 示例科技 | 一致 |
| 实习 | role | 后端实习生 | 一致 |
| 实习 | experience_type | internship | 一致 |
| 实习 | description | 维护测试脚本；成果：补充接口测试用例 | 一致 |
| 实习 | start_date | 2025-07 | 一致 |
| 实习 | end_date | 2025-09 | 一致 |
| 实习 | is_current | null（未知） | 未知保持正确 |

两项技能的 level、项目与实习的 is_current 共 4 个未知字段全部保持 null。它们不计作非空字段遗漏，也不能被解释为没有能力或已结束的确认事实。

## 4. 实际证据与定位

以下为模型返回后经领域映射保留的 21 条字段追溯，不是 Mock 证据。位置基于本报告 JSON 中 synthetic_pdf_extracted_text 的零起始字符区间，左闭右开；不是 UTF-8 字节偏移。

| 字段追溯 | 原文证据 | 第几页/段 | 字符区间 |
|---|---|---|---|
| education[0].school | 示例大学 | 2/5 | [96, 100) |
| education[0].degree | 本科 | 2/5 | [101, 103) |
| education[0].major | 计算机科学 | 2/5 | [104, 109) |
| education[0].start_date | 2022年9月入学 | 2/5 | [110, 119) |
| education[0].graduation_date | 预计毕业：2026年6月 | 2/5 | [120, 132) |
| education[0].graduation_status | 预计毕业 | 2/5 | [120, 124) |
| skills[0].name | 技能：Python。 | 2/6 | [134, 144) |
| skills[1].name | 技能：JavaScript。 | 2/7 | [145, 159) |
| projects[0].name | 课程选课系统 | 2/8 | [163, 169) |
| projects[0].role | 角色：后端开发 | 2/8 | [170, 177) |
| projects[0].start_date | 2024年3月 | 2/8 | [178, 185) |
| projects[0].end_date | 2024年6月 | 2/8 | [186, 193) |
| projects[0].technology_stack[0] | 技术栈：Python | 2/8 | [194, 204) |
| projects[0].work_items[0] | 编写课程查询接口 | 2/8 | [208, 216) |
| experiences[0].organization | 示例科技 | 2/9 | [221, 225) |
| experiences[0].role | 岗位：后端实习生 | 2/9 | [226, 234) |
| experiences[0].experience_type | 实习：示例科技 | 2/9 | [218, 225) |
| experiences[0].start_date | 2025年7月 | 2/9 | [235, 242) |
| experiences[0].end_date | 2025年9月 | 2/9 | [243, 250) |
| experiences[0].responsibilities[0] | 维护测试脚本 | 2/9 | [254, 260) |
| experiences[0].outcomes[0] | 补充接口测试用例 | 2/9 | [264, 272) |

教育范围为 [93,133)，项目为 [160,217)，实习为 [218,273)。Python 技能引用 [134,144)，项目技术栈引用 [194,204)，没有用项目中的同名词替换独立技能行。JavaScript 引用 [145,159)。

主评测及独立只读复核均直接重算 raw_text[start:end] == source_text，21/21 通过；非技能 19 条引用均位于独立 gold 的对应经历范围内，19/19 通过。字段原文引用增加上下文（如“2022年9月入学”“预计毕业：2026年6月”“岗位：后端实习生”）仍准确表达同一事实。

## 5. 字段指标、错误、遗漏与重点语义分析

| 指标 | 分子/分母或数量 | 结果 |
|---|---|---|
| 非空正式字段精确率 | TP=19；TP+FP=19 | 100% |
| 非空正式字段召回率 | TP=19；gold非空=19 | 100% |
| 错误/多余非空字段 | FP=0 | 无 |
| 漏抽非空字段 | FN=0 | 无 |
| 错误、额外、遗漏、重复候选 | 各0 | 无 |
| 未知值保持 | 4/4；未知字段擅填0 | 100% |
| 字段证据逐字定位 | 21/21 | 100% |
| 非技能证据范围归属 | 19/19 | 100% |
| 严重错误 | 0 | 未见错误被当 supported |
| 需要按gold修订的候选 | 0/5 | 0%（不是实际用户编辑成本） |
| 正确字段被护栏过度阻断 | 0 | 无 |

**错误候选与缺失字段：** field_errors、missing_fields、candidate_errors、concrete_error_cases 均为空。教育6、两技能各1、项目5、实习6个非空正式字段全部正确；未增加姓名、联系方式、雇主、技能水平、量化成果或其他事实。

**否定与计划语义：** 此份正常 PDF 的发送内容没有“尚未掌握 Java”“计划学习 Go”等否定或学习计划。模型没有凭空生成 Java/Go，但这只能证明本次没有新增这些事实，不能据此判定否定/计划能力通过。相关专门样本没有执行。

**毕业与未知语义：** 原文“预计毕业：2026年6月”正确形成 end_date=2026-06、end_date_kind=expected。即便评测日期已晚于该月份，也没有自动转换为已毕业。技能水平未擅填；项目/实习有结束日期仍保持 is_current=null，没有推断进行中或明确结束状态。

**经历归属：** Python 技术栈、编写课程查询接口属于课程选课系统；维护测试脚本与补充接口测试用例属于示例科技实习，未互相错挂。后端开发与后端实习生角色、两组起止年月均分别正确。此次只有一条教育、一条项目、一条实习，不能外推同类多项目/多实习交叉归属能力。

**证据与置信度限制：** V2 的 confidence 为必填字段，本轮21条追溯中的模型自报值均为0.5；它们只通过类型与范围校验，没有概率校准，不能当作事实正确概率。统一0.5也无法区分字段难易。supported、逐字定位与范围归属是护栏结果，均不证明虚构履历之外的真实用户事实。19/19是这份合成样本的观察成绩，不能作为整体模型正确率或上线保证。原模型JSON不另行保存，报告保留经Schema与领域处理的实际候选、raw_value和证据。

## 6. Token、调用状态与费用

| 计量 | 值 |
|---|---:|
| 输入 tokens | 1107 |
| 输出 tokens | 781 |
| 总 tokens | 1888 |
| 输出授权上限 | 4096 |
| 输入保守上界 | 16548 |
| 调用/预占/尝试 | 1/1/1 |
| 发送前金额上界 | 0.065864元 |
| 按usage峰值估算 | 0.008462元 |
| 批准预算 | 1元 |
| 实际账单金额 | 未确认（null） |

usage 来自真实供应商返回，并经原始类型/总量校验；输入和输出均低于授权边界。峰值估算为 `(1107×2 + 781×8)/1000000 = 0.008462 元`。未读取供应商账单，也未请求额外账单/Key验证接口；缓存命中、时段折扣及实际余额扣减未知，不能把估算称为确定账单。

## 7. 输入问题、安全处理与免费验证

最初精确收费确认未匹配，后续 getpass 输入也曾在本地 invalid_key 检查处停止；全部旧账本均 actual_calls=paid_calls=reserved_calls=0、cases=[]，不是模型失败后的收费重试。用户明确选择继续并要求换输入方式。某次 PowerShell 包装窗口没有进入 CLI，核对无 Python 任务/无账本后关闭；临时 PowerShell 包装脚本已移除。

用户曾把 Key 发到聊天。助手未将该内容复制到工具参数、脚本、环境变量或日志，并提示撤销；用户明确同意用新 Key 安全输入。供应商撤销动作未被独立验证。最终调用凭据由用户在本机遮蔽弹窗输入，本轮不保存凭据用于对照或回显。

用户报告使用 Ctrl+V 后，核查本机 getpass.win_getpass：若宿主把Ctrl+V传为控制字符，格式会失败；这是有源码依据的可能原因，不证明每次具体失败。用户随后确认右键粘贴不可用，明确要求更换输入方法。本轮新增仅用于本机评测的 Tk 密码弹窗，不增加依赖，不改变正式 Provider/Schema/领域逻辑。

弹窗始终 show="*"，支持用户普通粘贴，无明文切换、默认 Key、程序化剪贴板读取或持久化；复用正式 KeyRequest/SecretStr 校验。格式失败清空，三次失败、五分钟超时、取消或 Tk 异常安全停止；回调异常禁用默认 traceback，清理失败不会返回 Key。成功清空并销毁弹窗后交给既有会话 BYOK/Vault。

两项非秘密确认仅基于本轮已经明确授权的费用和未变化发送SHA，按CLI精确提示各填写一次；变化或额外提示拒绝。整个流程没有自动代填Key。包装器在任何后续启动前读取所有历史本轮账本，任一调用/预占非零或cases非空即拒绝；最终已有一次真实调用，不能重启。

免费验证：单样本plan及PDF/全文/Prompt一致性通过；原生Key安全专项14/14通过；本机弹窗遮蔽/清空/取消通过，另正常输入、三次格式失败、超时、回调异常4项通过（只用合成校验值，未输出Key，Provider=0）；新增评测模块AST检查通过。首次从backend目录执行unittest发现tests位于根目录，未加载测试，纠正后14/14通过。没有把既有Mock成绩或历史全仓成绩当作本轮真实成绩。

交付新增本机评测安全输入辅助、启动/失败账本和本报告，并在PRD/架构/数据库/路线图追加本次结果链接；业务代码、依赖及迁移未修改。

**收尾限制：** 所有本轮评测进程均已退出。检查发现早期关闭getpass输入进程后留下 `C:\Users\13432\AppData\Local\Temp\offerpilot-pdf-evaluation-4fhuj5wt`；创建时间比本轮对应启动晚2.53秒。只读核验其PDF hash及正文与同一合成样本完全一致，resume_extraction_runs/profile_candidates/career_profile_entries行数均为0，停止点在Key输入完成前。删除请求被自动审批拒绝，工具仅给出 `blocked by policy`，没有更具体理由。没有改用其他命令绕过，目录仍保留；该限制不影响唯一成功模型运行的结果、其正常退出清理或请求额度，但不能宣称全部早期临时目录已清零。

## 8. 原始记录与最终停止点

- [唯一真实结果、实际候选与全部证据 JSON](../backend/evaluations/s2-2b-single-real-dialog-report-2026-10-14.json)
- [最终安全输入状态](../backend/evaluations/s2-2b-single-real-dialog-state-2026-10-14.json)
- [最终启动记录](../backend/evaluations/s2-2b-single-real-dialog-launch-2026-10-14.json)
- [免费PDF预检](../backend/evaluations/s2-2b-single-real-preflight-2026-10-14.json)
- [续跑免费预检](../backend/evaluations/s2-2b-single-real-continued-preflight-2026-10-14.json)
- [发送前安全核查](../backend/evaluations/s2-2b-single-real-safety-audit-2026-10-14.json)
- [弹窗免费检查](../backend/evaluations/s2-2b-masked-key-dialog-free-check-2026-10-14.json)
- [弹窗免费边界结果](../backend/evaluations/s2-2b-masked-key-dialog-boundaries-2026-10-14.json)
- [原生输入失败固定错误码](../backend/evaluations/s2-2b-single-real-secure-input-2-state-2026-10-14.json)

首次及各次输入失败账本完整保留；唯一真实调用完成后进程退出，本轮后续仅离线复核报告。其余四份、S3、推送、部署均未执行。单样本结果不解除供应商BYOK/训练保留、真实个人资料脱敏、生产部署、多worker、账户导出/注销与备份删除等既有发布阻塞。
