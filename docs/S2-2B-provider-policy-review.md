# S2-2B 供应商协议、BYOK 与隐私审查

核查日期：2026-10-12（Asia/Shanghai）。审查对象：当前本机 S2-2B 代码及 DeepSeek 官方中文政策。仅进行公开资料读取和免费测试；实际 DeepSeek 请求 **0 次**。

**结论：本机安全边界可继续接受免费验证；不能据此开放第三方服务器 BYOK 或处理真实个人简历。** 负责人后续明确授权次数与预算后，可先在本人控制的本机、使用本人 Key 对纯合成样本做独立技术评测。该评测不解除下列发布阻塞，也不代表已获供应商代理使用许可。

## 1. 官方依据及无法确认的事项

| 官方资料 | 本次核实内容 | 对 OfferPilot 的判断 |
|---|---|---|
| [开放平台服务协议](https://cdn.deepseek.com/policies/zh-CN/deepseek-open-platform-terms-of-service.html)，2026-04-22 更新、2026-04-29 生效 | 1.1 允许按约定集成下游应用；2.4 要求妥善保管、不共享或公开 Key、不放入客户端代码 | 下游集成条款没有明确说明终端用户将 Key 交给独立运营者服务器的例外；不能推断本产品 BYOK 已获允许，需确认主体、用途和托管方式 |
| 同上，3.2、3.5、4.1、5.5 | 下游运营者承担数据处理责任；应披露规则，为个人信息及委托处理取得同意或其他合法基础，并拥有输入处理权限；供应商隐私政策不能替代下游规则 | 用户勾选外传和费用是已有技术机制；正式主体、隐私规则、第三人信息及权利请求流程仍需发布前落实 |
| [隐私政策](https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html)，2026-02-10 更新并生效 | 一般适用范围包括 API；智能对话输入/输出可能经处理用于训练，产品设置提供退出；第五节为目的所需保留并列举例外，未给 API 输入/输出统一天数 | 未找到 API 专门训练退出开关、设置覆盖哪个 Key/接口的证明、确定保留天数或零保留承诺；当前不能宣称“API 不训练”或“零保留” |
| [上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/) | 默认为请求构建落盘缓存；不再使用后通常数小时至数天清理 | 缓存清理不是全部日志、滥用监测、备份及训练数据的删除承诺；当前未核实产品可用的关闭缓存方式 |

以上是官方文本的核对和本项目风险判断，不代替供应商对具体使用方式的书面确认。没有向供应商发送邮件或提交工单。

## 2. 代码核查

| 边界 | 实际代码与结果 | 限制 |
|---|---|---|
| 身份与跨用户隔离 | `identity/dependencies.py` 从服务端 Cookie/数据库解析 user/session；修改验证 CSRF/Origin；Vault 同时核对 user、session、Key reference；简历和任务校验 owner | 不接受客户端归属作为授权；供应商账单仍属于 Key 所在账户 |
| Key 保存 | `agent_runtime/byok.py` 使用进程随机 Fernet 主密钥，加锁、容量上限 100 的限时密文内存；TTL 60～3600 秒且不超过登录会话 | 不落业务数据库；SDK 调用阶段必然短暂存在明文，不能保证 Python 堆零残留；进程完全失陷、转储/交换文件防护未验收 |
| 撤销与失效 | 退出、重新登录轮换、换 Key、撤销清除引用和预览；解析 Key 及入库前检查会话到期/撤销、账户 active 和密码指纹；旧引用不会撤销新 Key | OfferPilot 撤销只是停止本产品继续使用；不是删除/撤销 DeepSeek 账户中的 API Key，泄露时用户仍需在供应商控制台撤销 |
| 单进程 | API 内同进程执行器解析临时 Key；无 Vault 的独立 CLI 仅处理 Mock；重启失去 Key，读取恢复/执行令孤立真实任务安全失败 | 不支持多 API worker/独立真实 Worker；环境 worker 非 1 拒绝开启；命令行强制多 worker、跨主机密钥方案仍是部署限制，不能把环境检查当完备部署保护 |
| 发送预览 | 预览保存脱敏文本与 SHA-256，绑定 owner/session/ref/简历版本；执行重新脱敏并验 hash，`extractor_v2.py` 将同一文本作为 JSON `resume_text`；前端显示 `preview.redacted_text` | PDF 原件不发送。线上请求另包含版本化固定抽取 Prompt、JSON 包装和遮罩说明；预览展示的是简历文本内容，而非 HTTP 完整请求。不能声称整个请求体逐字可见 |
| 数据最少必要 | 章节白名单、无关区/已识别个人资料行等长遮罩；联系资料/网址遮罩；未识别有效章节拒绝 | 词法规则不能全面识别 PII，经历中未标注姓名、第三人信息及混排仍可能外传；预览检查和纯合成默认样本必须保留 |
| 授权和账本 | Key 配置、免费预览、真实任务独立；精确 `accepted=true/max_calls=1`；调用前写持久次数与费用 unknown；同授权不可另建任务、真实 retry 拒绝、迟到事实被围栏阻断 | 取消/超时/撤销无法撤回已发送请求，usage 不是实际账单；响应丢失仍可能收费 |
| 日志/存储 | 验证错误不回显输入，API `no-store`；运行表只保存必要引用/版本/hash/token，未存 Key、Prompt、原响应；前端发送后清空 Key，无持久浏览器存储 | Next 同源代理也短暂接收 Key；反向代理/APM/抓包、HTTPS、目录 ACL、转储及正式保留/删除策略需部署验收 |

审查文件：`agent_runtime/byok.py`、`extraction_runs.py`、`extraction_schemas.py`、`resume_ingestion/extractor_v2.py`、`api/routes/byok.py`/`extraction.py`/`auth.py`、`identity/services.py`/`dependencies.py`、`core/config.py`、`main.py`、前端 `ai-resume-panel.tsx`/`proxy.ts`。本轮未引入跨进程 Vault、持久 Key 或第二套认证系统。

## 3. 免费验证与审查限制

既有 `tests/test_byok_security.py` 覆盖密文/不回显、跨用户和会话隔离、过期/撤销/密码及账户失效、CSRF、显式授权、换 Key 后预览失效、并发调用及准入、取消/删除/退出/版本变化迟到、重启/租约恢复与无付费重放。免费协议/故障测试另见 `tests/test_deepseek_paid_boundaries.py`。

本审查重跑 `backend/.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_byok_security.py -q`：**15/15 通过，14.174 秒**；全部使用临时数据库/Fake Provider，不联网调用模型。其余免费测试由 [正式模型验收准备报告](S2-2B-readiness-review.md) 汇总；已有 S2-2B 基线为后端 255、前端 49、Chromium 18 全通过。本次代码核查不将基线 Mock/Fake 结果解释为供应商网络或隐私配置已实测。应保持直接断言 Provider 的 JSON `resume_text` 等于授权预览的回归验证。

本轮已补 `tests/test_deepseek_preview_consistency.py`，专项 **6/6 通过**并纳入最终全量：真实登录/API/临时 SQLite 加捕获 Provider 桩，直接断言 JSON `resume_text` 与预览逐字相等、hash相等和联系信息遮罩；原文/脱敏变化、旧模板排队任务均为零调用；旧 V2 来源仍可读取与审核。2026-10-14 收尾没有解除上述供应商或生产阻塞。

## 4. 发布阻塞及关闭条件

1. **第三方服务器 BYOK 许可：** 负责人取得针对用户 Key 经 OfferPilot Web/API 接收、短时内存保存和代理调用方式的适用条款或供应商确认；明确账户类型和责任主体。不能以加密、短时保存或用户自愿替代许可依据。
2. **API 数据处理配置：** 明确 API 输入/输出是否进入训练、退出生效范围和可核验方式；明确日志/缓存/备份等保留与删除范围、地区及例外。在确认前不向真实用户承诺不训练、零保留或撤销后供应商删除。
3. **下游个人数据责任：** 批准正式告知/授权及权利响应流程，覆盖简历里可能存在的第三人/雇主信息、供应商处理和已发请求无法撤回；完整账户导出/删除与备份限制仍未交付。
4. **正式部署安全：** 批准 HTTPS、私有文件权限、无正文/Key 的日志/APM、内存转储及单进程方案；多 worker/独立真实 Worker 需要专门密钥设计和隔离验收，当前持续关闭生产真实模式。
5. **真实质量：** 正式模型字段/证据/归属、否定/计划/日期歧义/提示注入和实际 usage/费用须另行获授权后验收；免费桩没有提供这些成绩。

这些事项不要求本轮联系供应商、执行收费评测、部署或开发 S3。完成免费准备后停止。
