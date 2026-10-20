# S2-2B 正式模型验收准备与兼容性审查

官方初审：2026-10-12；收尾及最终后端回归：2026-10-14（Asia/Shanghai）。分支 `feature/resume-agent`，开始时 HEAD 为 `78aecf8`，有效 S2-2B 开发内容尚未提交，全部保留。

**结论：免费验收准备完成，真实收费验收仍未执行。实际 DeepSeek 请求 0 次，付费调用 0 次，费用 0 元。** 后续负责人明确授权、次数上限和预算后，可先使用本人 Key、本人控制的本机 CLI 对一份纯合成样本做技术评测。第三方服务器 BYOK、真实个人简历处理及公开发布仍受供应商和隐私阻塞限制。没有进入 S3/S2-3/Browser、推送、部署或迁移实际开发/生产数据库。

开始前阅读 PRD、architecture、database、roadmap、最新 PLAN/README、S2-2B 验收、Adapter/锁文件、任务运行与授权机制、评测数据及 Git 状态。此前 255/49/18 的成绩保留为历史基线，不用免费桩成绩代替真实模型质量。相关文件以本轮本地检查点保存，实际提交 SHA 在最终交付中列出，可用 `git log -1 --oneline` 复核。

## 1. 官方兼容性结论

| 检查项 | 官方依据与当前实现 | 判断 |
|---|---|---|
| 废弃模型 | [官方旧模型停用公告](https://api-docs.deepseek.com/zh-cn/news/news260424/)注明 `deepseek-chat/reasoner` 于 2026-07-24 停用；[V4.1 更新记录](https://api-docs.deepseek.com/updates/)说明新请求模型为 `deepseek-flash` | 产品运行和新评测均使用 flash。旧模型名仅见历史资料/测试响应占位，不触发旧接口；旧环境 Key 收费 CLI 继续禁用 |
| 模型与返回格式 | [Chat Completions API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)和[模型表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)列出 flash、ChatCompletion 结构及 usage；当前文档对应 DeepSeek-V4.1-Flash | 固定官方地址，`stream=false`，读取 `choices[0].message.content`。记录请求与安全筛选后的响应 model；别名不能证明实际权重版本 |
| 非思考模式 | 官方 thinking 默认 enabled，可设 disabled | 实际序列化发送 `thinking:{type:disabled}`，由 SDK `extra_body` 扩展。没有依靠默认值关闭思考 |
| JSON Output | [官方 JSON 指南](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)要求 json 指示、格式示例和足够输出上限，也提示空响应风险 | `json_object`、明确 JSON 指令、完整 V2 四组格式示例；格式合法仍须本地 Schema/证据验证 |
| SDK 与上限 | 锁定 openai 2.54.0、httpx 0.28.1、httpcore 1.0.9；本轮未新增依赖或升级 SDK | 产品及真实评测设置 I/O timeout 10 秒、max_tokens 4096；读取 deadline 40 秒、未压缩响应 1,000,000 字节；运行租约 60 秒阻断迟到入库 |
| usage | 官方有输入、输出、总数及缓存明细字段；缓存和峰谷价影响账单 | 校验原始 JSON 的精确 int 类型、非负范围和总数；拒绝 SDK 强转后的虚假计量。缺失/畸形保持 unknown，usage 不等于账单；估算使用负责人核实的最高单价，不假定缓存命中 |
| Schema V2 | JSON 模式不保证业务 Schema、证据或事实正确 | 官方 SDK 无网络协议桩可通过严格 Schema 2.0；无效 JSON、V1、缺叶、额外字段均一次失败。真实模型是否稳定满足 V2 **未验收** |
| 自动重试 | SDK/HTTP 原本默认行为不能仅凭调用一次函数推断 | SDK `max_retries=0`、transport `retries=0`、不跟随重定向；真实抽取 `max_attempts=max_calls=1`，旧真实任务禁止 retry。HTTP/超时/断网故障桩均只有一次请求 |
| 供应商内部行为 | [限速与隔离](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit/)记录空行保活和最长等待推理行为 | 未找到供应商内部零重调度/零推理重试保证。只能确认本应用/SDK不补发；超时、取消或响应丢失仍可能收费 |

响应 deadline 和租约不是强制杀死任意 OS/DNS 阻塞线程的 SLA。真实网络参数、时延、供应商服务状态及账单须在以后获授权的单次评测核实。

## 2. 实际发现及最小修复

1. **畸形响应漏出裸异常。** 锁定 SDK 对 `choices` 字符串、空 message、非文本 content 的宽松解析可能触发 `AttributeError`；Adapter 增加形状校验及安全错误 `provider_invalid_response`，不保存响应正文、不补发。
2. **usage 类型检查被 SDK 强转绕过。** 真实 SDK 会将字符串、布尔值及浮点数转成 int；同一次 SDK 请求改用 raw response 读取原始 JSON usage 后严格核验。无效计量保持未知，合法 usage 在输出随后被拒绝时仍可保留。
3. **压缩解码绕过大小声明。** 原流上限统计压缩字节；现请求 `Accept-Encoding: identity` 并拒绝非 identity 响应编码，保留 TLS 和不重定向边界。超限安全失败且不自动重试。
4. **V2 示例不完整及版本追溯。** 原 Prompt 只有叶子形状，补完整四组纯合成示例及未知 null/空数组要求，升为 `resume_extract_v2.1`；Schema 仍 2.0、抽取版本升为 `resume-extraction-v2.1`、Provider 版本为 `deepseek-openai-json-v1.1`。旧 V2 来源仍可读取/审核；旧模板 queued 任务在构造 Provider 前失败，调用数不增加。同步生成 OpenAPI 客户端类型，无新数据库字段或迁移。
5. **评测准备不够明确。** 从旧六样本准备改为五份代表性合成白名单，增加默认首份、免费 mock 模式、负责人授权记录、执行当天价格核查、新账本、独立证据归属、错误候选及预占/实际边界计数。账本写入失败及输入提前拒绝不会被计作实际调用。

没有更换 Adapter 契约、重构 AI 架构、引入大型 Agent 框架或覆盖已确认事实。新增验证使用真实锁定 SDK 的 HTTP MockTransport、真实登录/API/临时 SQLite、固定协议响应和故障注入。

## 3. BYOK、隐私及协议阻塞

详细责任、官方条款位置和关闭条件见 [供应商协议审查](S2-2B-provider-policy-review.md)。[开放平台协议](https://cdn.deepseek.com/policies/zh-CN/deepseek-open-platform-terms-of-service.html)为 2026-04-22 更新/04-29 生效；[中文隐私政策](https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html)为 2026-02-10 版本。本轮没有取得新的合同许可或联系供应商。

- 下游集成许可与 Key 不共享要求不足以确认第三方 OfferPilot 服务器接收用户 BYOK 获准；需供应商适用条款或书面确认。
- 下游运营者须为简历及其中第三人信息承担告知、合法处理基础、供应商委托处理和权利响应责任；产品勾选机制不替代正式处理协议及隐私规则。
- 未确认 API 专门训练退出的适用范围/可验证开关、确定保留天数或零保留。硬盘缓存的数小时至数天清理不是全部数据删除承诺。
- 当前 Key 在单 API 进程随机 Fernet 密文内存中最长一小时且受会话期限约束；跨用户/会话/ref、撤销、换 Key、过期、账户/密码失效及迟到结果围栏通过。内存加密不证明零残留或抵抗进程失陷，不支持多 worker/独立真实 Worker。
- 精确验证模型 JSON `resume_text == preview.redacted_text` 及 SHA-256；完整请求另含固定 Prompt 和包装说明。词法遮罩仍可能漏掉混排和第三人信息，不能承诺全面匿名化。
- 撤销是本产品引用撤销，不是供应商控制台 Key 撤销。HTTPS、ACL、日志/APM/转储、账户导出/注销、备份和供应商删除流程仍属公开发布前条件。

这些事项保持发布阻塞；负责人使用本人 Key、本机纯合成 CLI 做独立技术评测，不代表下游 BYOK/真实个人数据条件已经放行。

## 4. 可重复入口与权限开关

仓库根目录免费运行：

```powershell
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py --mode plan --case-limit 5
& .\backend\.venv\Scripts\python.exe -B backend/evaluations/evaluate_deepseek_v2.py --mode mock
& .\backend\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_deepseek*.py' -v
```

默认 plan 不构造 Provider、不读取 Key、只规划第一份。mock 不建立真实 transport。默认真实 API 功能开关 `OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED=false`；生产无法启用，CLI 不依赖这个开关或应用 Vault。设置开关不构成收费测试授权。

完整后续收费命令在 [执行手册第3节](S2-2B-real-evaluation.md)。必须有未来负责人实际授权，同时满足 `--mode real --authorize-paid-calls --authorization-record`、`--max-calls`、`--max-output-tokens`、`--max-budget-cny`、输入/输出峰值单价、当天 `--price-audit-date`、全新账本、交互确认和 getpass Key。不能读取环境/命令行 Key、任意文件或真实简历；报告存在就拒绝重放。CLI 标识用于追踪，不是外部签名授权系统，不能以随机标识代替实际批准。

调用前 fsync 预占账本，计数在 Provider 边界增加。任何结构/证据严重错误、空候选、usage 未知或越界、超时/网络/HTTP失败均停止本批后续，不补发。报告记录请求/响应模型、官方版本快照、SDK/Provider/Prompt/hash/Schema/脱敏/样本版本、字段 Precision/Recall、错漏及错误候选路径、证据定位/归属、token、预占和实际尝试计数。实际金额留 null，须负责人核对账单。

## 5. 推荐的小规模收费方案（本轮未执行）

五份固定合成样本为 `normal_chinese`、`negation_planned`、`multiple_entries_dates`、`year_time_ambiguity`、`injection_input`，分别覆盖正常四组、否定/计划、多经历归属、毕业年份/季节歧义和简历注入。数据及独立 gold 见 [样本](../backend/evaluations/deepseek_v2_cases.py)。样本共享基础内容，不是独立真实用户简历分布。

先只授权 `normal_chinese --max-calls 1`；人工核对 JSON、V2、证据/归属、usage 和账单。负责人决定继续后，建议剩余四份各用新授权记录、新预算和新账本逐份运行，每批一次。任一失败先修复并免费回归，再决定新的收费批次；不自动循环或复用失败账本。

建议护栏为严重可确认错误 0、注入/否定/计划/未知日期不变成已确认事实、被接受字段的证据定位与归属100%。PLAN 的 Precision≥95%、Recall≥85%只是待负责人批准的建议；必须报告分子/分母、逐字段错漏和人工评审，小样本通过不构成上线保证。

## 6. 全部免费验证结果

最终结果包含 2026-10-12 前端/浏览器验证及 2026-10-14 收尾后的后端全量；后续只有文档调整，没有新增业务变更。

| 验证 | 结果 |
|---|---|
| `python -B -m unittest discover -s tests -q`，development / 内存默认DB，测试各自临时库 | **286/286**，87.839 秒，失败0/跳过0；保留基线255，新增31 |
| Provider 官方 SDK 协议/故障专项 | **13/13**，纳入全量；wire参数、usage原始类型、压缩拒绝、保活、Schema一次失败及零补发 |
| 预览/版本/旧来源兼容专项 | **6/6**，纳入全量；发送逐字一致、source/redaction/旧Prompt零调用、旧来源可审核 |
| 评测质量/权限/计数专项与原收费边界 | **22 + 6**，纳入全量；当天价格、授权/预算/白名单/账本、错漏/归属与失败止步 |
| `npm run test` | **49/49**，11.23 秒，失败0/跳过0 |
| `npm run test:e2e`，真实本机 FastAPI/Next/Chromium，AI仅私有故障桩 | **18/18**，1.4 分钟，失败0/跳过0 |
| `npm run api:check`、`typecheck`、`lint`、`build` | 通过；契约/类型在收尾后再次核验，生产构建仅本机验证 |
| `tests/http_file_lifecycle_smoke.py` | exit0，真实 localhost HTTP 生命周期通过；服务和临时库清理 |
| 五份 `evaluate_deepseek_v2.py --mode mock` | Precision/Recall **112/112** 非空字段；逐字定位 **127/127**，独立经历范围归属 **113/113**；严重错误0；协议桩5次，真实/收费0次 |
| 默认 plan | exit0，首份计划、实际/收费0，不构造 Provider |
| 既有11份 `evaluate_resume_extraction_v2.py` | exit0，146非空正确/1错/1漏，null正确41；位置161/163、护栏失败0；故意错误输出的差异仍保留 |
| Python语法及项目/锁文件 | **86**个 Python 文件 AST 与 pyproject/uv.lock TOML 解析通过；仓库没有额外后端 formatter/typecheck 命令，未安装新工具 |
| Git差异、旧迁移与秘密/清理核查 | `git diff --check`通过；旧0001～0005保持不变，本轮没有新增迁移；变更文件未发现Key形态值/秘密配置，E2E端口3001/8001监听数0 |

固定协议报告见 [免费报告](../backend/evaluations/s2-2b-free-preflight-report.json)。其中模拟 token 明确标记测试，100%结果只验证固定响应/映射/定位，不证明真实模型准确率。现有 Starlette TestClient/httpx 弃用提示为非阻塞依赖兼容债，本轮未为消除提示升级依赖。

本轮临时库/HTTP服务清理通过。只读检查另发现2026-10-11的历史 `offerpilot-link-test` 临时目录，不属于本轮生成内容，未删除或改写；没有将其计作本轮测试已清理的目录。Windows Git 的 LF/CRLF 转换提示不影响默认仓库配置下的差异检查，未做全仓换行重写。

## 7. 停止与后续判断

**可以作为负责人下一次明确授权的一份纯合成收费技术评测的准备基线；不能认定真实收费验收通过，也不能开放真实个人简历或公开 BYOK 服务。** 本轮没有后续收费授权，未读取真实 Key、未调用付费 API。供应商许可/API训练保留/正式隐私与部署条件、真实模型质量和网络账单仍待确认。完成文档与本地检查点后停止，不进入 S3。
