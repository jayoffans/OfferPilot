# S2-2B 首次真实模型评测：零费用预检

日期：2026-10-14（Asia/Shanghai）。基线：`feature/resume-agent` / `aad33bc7b25883e9e46be18b22f12c0c6e1621cc`，开始时工作区干净。

**结果：FAIL，停止在第一阶段。真实 DeepSeek HTTP 请求 0 次、收费调用 0 次、费用 0 元。** 用户明确本提示不构成收费授权，且任一检查失败必须停止报告。因此没有申请收费授权、执行真实命令、读取真实 Key、修改业务代码、迁移实际数据库、推送、部署或进入 S3。

此前免费验收基线 286/49/18 保持有效，但不能证明现有纯文本 CLI 能完成本次新增要求的 PDF→预览→单次真实模型→待审核池闭环。本轮仅新增本预检报告；以下失败没有被静默修复或绕过。

## 1. 九项检查

| 项目 | 结论 | 证据与限制 |
|---|---|---|
| 1. deepseek-flash / 非思考 | PASS | Provider 与 CLI 固定请求 flash，wire 参数 `thinking.type=disabled`；[官方模型表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)对应 DeepSeek-V4.1-Flash |
| 2. JSON Output / Schema V2 | PASS（免费协议层） | `json_object`、完整版本化 Prompt 与严格 Schema 2.0；无效 JSON/V1/缺叶/额外字段一次失败。真实模型是否正确抽取仍未验收，JSON 成功不等于准确 |
| 3. SDK / HTTP / 业务重试 | PASS | SDK max_retries=0、HTTP retries=0、不重定向；业务真实 max_attempts=1、旧真实 run 禁止 retry。故障协议桩验证只有一次请求 |
| 4. 本次最多一次请求预算 | PASS（现有门槛可约束） | CLI 可设 max-calls=1，调用边界计数、调用前 fsync 预占和旧账本拒绝重放；未启动任何本次真实运行。后续必须将审批、具体样本与新账本绑定，不能扩大为其余样本 |
| 5. Key不进日志/截图/DB/Git | 未完全通过 | 产品 Vault/Adapter 不保存或回显 Key；本轮测试仅使用合成 Fake Key，无截图/真实凭据。但 CLI 普通 getpass 缺少对无法隐藏输入环境的 fail-closed 保护，不能将未验证的管道终端作为安全输入入口 |
| 6. 脱敏后发送与本次预览一致 | FAIL（CLI入口） | 产品 API 有完整预览及 hash 围栏，免费回归可证明逐字一致；CLI 的 plan 只有 hash/coverage，真实模式只显示样本数/金额，没有用户可审核的最终 resume_text 预览，不能替代本次预览 |
| 7. 未确认不写正式画像 | PASS（现有领域边界） | 产品运行只创建 pending，正式写入必须 review。当前 CLI 不访问数据库，因此不会写正式画像；但也不能验收本次真实结果进入 pending 池，见下节 |
| 8. 超时/连接中断费用未知且不补发 | PASS | 业务调用前记 unknown，传输/超时只尝试一次；缺失 usage 保守保留未知，失败停止后续，不重新请求 |
| 9. 所选PDF完全合成 | FAIL（缺少选定PDF） | normal_chinese 文本可确认完全合成，但现有 CLI 不接受/读取 PDF，仓库未找到已选定测试 PDF。没有本次 PDF 字节、hash、提取布局及发送文本对应证据，不能把合成字符串当成所选 PDF 已验收 |

## 2. 具体阻塞

1. **缺少PDF路径。** `backend/evaluations/deepseek_v2_cases.py:3`明确不提供文件导入；`evaluate_deepseek_v2.py:344`直接读取 `case['text']`并构造布局。没有本次选定PDF的生成/校验/解析路径。
2. **缺少本次CLI发送预览。** `evaluate_deepseek_v2.py:281～283`只记录样本及发送 hash；`:312`显示统计金额，`:316`读取 Key；没有展示完整脱敏文本并将该预览绑定到本次真实输入。产品 API 预览测试的成功不能替代该入口中的实际行为。
3. **真实结果不进入待审核池。** 当前 CLI 只计算 quality 并写 JSON 报告，没有调用候选入库/运行完成服务、隔离评测数据库或正式画像查询。因此无法在同一次真实输出下验证 pending 数量、pending 状态与正式条目仍为空。
4. **退出后无法复核具体错误值与证据。** `evaluate_deepseek_v2.py:112`等位置只存错误 path/kind/status，`:390`明确不保存原模型输出。既不保存最小候选值/证据，也没有待审核池承接，无法从现有报告复原实际错误值、遗漏及引文案例。应保留足够的纯合成候选审查数据，同时继续排除 Key、请求头和秘密；不要求记录完整敏感请求。
5. **安全Key输入环境没有失败关闭。** `evaluate_deepseek_v2.py:316`使用普通 getpass，没有安全终端条件或 GetPassWarning 拒绝逻辑。Python 的 getpass 在不支持隐藏输入时可能警告并回退普通读取；这是潜在暴露风险，本轮未输入真实 Key，未发生已知泄露。

现有正常文本为示例大学、Python/JavaScript、课程选课系统及示例科技后端实习，无姓名或真实联系方式。独立 gold 为5个候选、19个非空正式字段；固定协议响应有21条字段证据。它可以作为后续合成PDF来源，尚不是本次已选PDF的验收证明。

## 3. 已完成的免费专项

| 验证文件 | 结果 | 外部请求边界 |
|---|---:|---|
| tests/test_deepseek_compatibility.py | 13/13 | 真实锁定 SDK，HTTP transport 替换为 MockTransport |
| tests/test_deepseek_paid_boundaries.py | 6/6 | Fake client/Provider，Key交互亦为Mock |
| tests/test_deepseek_preview_consistency.py | 6/6 | 临时 SQLite、真实API、捕获 Provider 桩 |
| tests/test_deepseek_evaluation_quality.py | 22/22 | 固定响应和授权/账本/计量故障注入 |
| 合计 | **47/47** | **真实/收费0次** |

开始发现失败前已启动的免费专项正常结束并清理；没有继续扩大测试或实施修复。现有 Starlette TestClient/httpx 弃用提示不影响结果。没有重跑全部286/49/18，也不将历史全量数字当成本轮新执行结果。

2026-10-14 查阅[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)与[Chat API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)：flash 峰值输入未缓存2元/百万token、输出8元/百万token，空闲时段为一半。因为未通过预检、未固定实际PDF发送范围，本报告不生成本次收费费用上界或收费启动命令，也不请求授权。实际供应商内部调度、超时扣费及账单仍不能由免费桩证明。

## 4. 停止点

需要先准备一个隔离的单样本验收入口：固定纯合成PDF及其hash/提取文本/最终预览；最多一次官方HTTP请求；同一响应进入pending池并验证正式条目为空；保留可复核的纯合成候选错误/证据；Key在可验证隐藏输入环境中失败关闭。完成这些准备并重新免费预检后，才进入第二阶段给出具体费用和命令，再由负责人明确授权。

上述为阻塞及关闭条件，**本轮没有实施这些改动，也没有收费授权请求。** 按用户要求在第一阶段停止；不自行扩大样本、进入S3或把预检失败改称通过。
