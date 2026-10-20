# Resume Extraction Agent 真实 API 评估记录

本记录跟踪通过真实 DeepSeek API 对当前 Resume Extraction Core 的本地评估。评估输出含简历个人信息，只打印到终端；不要把原始 JSON、完整简历文本、PDF 路径、API Key 或联系方式复制进仓库。

## 运行方式

在运行环境设置 `OFFERPILOT_DEEPSEEK_API_KEY`（或 `DEEPSEEK_API_KEY`），不要把 Key 写入命令、`.env.example`、文档或版本控制。然后在仓库根目录运行：

```powershell
Set-Location backend
uv run --project . python -m evaluations.evaluate_resume_extraction "C:\private\resume.pdf"
```

可用 `--model MODEL_NAME` 指定模型；默认读取 `OFFERPILOT_DEEPSEEK_MODEL`，未设置时用 `deepseek-flash`。脚本调用现有 PDF 文本提取逻辑和 `extract_student_profile`，只向标准输出打印完整 Extraction JSON，不写数据库或文件。

评估诊断以脱敏 JSON 输出到标准错误流：包含每次 LLM 调用状态、HTTP 状态码、Provider 异常类型、安全错误摘要、Schema 错误路径、Evidence 失败字段、confidence 越界字段及重试次数。错误摘要按状态码生成，不直接输出供应商原始错误正文；诊断不包含 API Key、请求内容、简历文本或模型响应。失败时以失败类别和诊断信息说明原因。

## 评估记录

| 项目 | 记录 |
|---|---|
| 测试样本 | 本次未读取或发送；用户提供的路径预检未找到文件。 |
| 模型版本 | 未调用；默认模型配置为 `deepseek-flash`。 |
| Prompt 版本 | `resume_extract_v1` |
| Schema 版本 | `StudentProfileExtractionV1` / `1.0` |
| 执行日期 | 2026-09-25（预检） |
| 模型返回结果 | 无；未发出 API 请求。 |
| 正确/错误识别与幻觉 | 未评估。 |
| `source_text` 与原文匹配 | 未评估。 |
| `confidence` 合理性 | 未评估。 |
| 发现的问题 | 指定 PDF 路径不可访问，且执行环境中未配置 `DEEPSEEK_API_KEY`；待补齐后重新运行。 |

## 当前已知限制

- 必须是可检索文本的 PDF；扫描件、加密 PDF 或无法提取文本的文件会失败。
- 抽取核心限制输入文本长度为 50,000 字符，最多进行两次模型调用（首次及一次校验重试）。
- DeepSeek JSON Output 只保证 JSON 形式；Schema 与证据校验失败时脚本安全失败，不会保存部分结果。
- 真实评估需要外部 API 处理简历文本。只使用获得授权的测试样本；评估输出中的字段仍是待用户确认的候选事实。
