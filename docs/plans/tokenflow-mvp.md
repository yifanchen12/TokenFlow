# TokenFlow MVP

## Requirement reading

第一版交付一个本地可运行的 TokenFlow MVP：接收任务和文本内容，生成简单执行计划，进行确定性的本地内容压缩，并展示传统处理与 TokenFlow 处理的估算 Token 差异。

Laya 和 Jev 只保留后续可接入的位置。本版不依赖外部模型、API、数据库或前端框架。

## Reuse survey

- Reusing: Python standard library - HTTP 服务、JSON、正则、命令行参数和语法编译均可覆盖第一版需求。
- Gap needing new code: 当前工作区没有源码、依赖清单、构建脚本或测试。
- Affected callers / entry points: `tokenflow.py` 的命令行入口和 HTTP API；`start_tokenflow.cmd` 的 Windows 一键启动入口。
- Existing tests: 无。
- Build/test commands: `python -m py_compile tokenflow.py`；`python tokenflow.py --self-check`。

## Capability and file boundaries

- 本地任务识别与计划生成：
  - `tokenflow.py` - 根据任务和内容生成文档、代码、表格或通用类型，并生成执行步骤。
- TokenFlow 压缩与指标：
  - `tokenflow.py` - 清洗内容、进行确定性截取压缩、估算 Token、计算节省量和节省率。
- 本地 Web 入口：
  - `tokenflow.py` - 提供首页、`/health` 和 `POST /api/run`。
  - `start_tokenflow.cmd` - 编译并启动本地服务。
- 使用说明：
  - `README.md` - 说明启动方式、接口和第一版边界。
- Unchanged: 外部案例 DOCX 和聊天附件 - 只作为需求依据，不修改。

## Interfaces and invariants

- `run_workflow(task, content) -> dict` - 执行完整本地流程；任务或内容为空时返回 HTTP 400 对应的校验错误。
- `estimate_tokens(text) -> int` - 返回明确标注为估算值的 Token 数，不声称等同于具体模型 tokenizer。
- `POST /api/run` - 接收 `{\"task\": string, \"content\": string}`，返回任务类型、步骤、原始/压缩估算 Token、节省量和压缩结果。
- `/health` - 返回 `{\"status\": \"ok\"}`。
- 不调用云端服务，不读取环境变量中的密钥，不把外部模型作为启动前提。

## Build order

1. 实现纯本地处理函数，并通过 `--self-check` 验证识别、压缩和指标计算。
2. 组装 HTTP handler，验证 JSON 请求和错误响应。
3. 接入命令行入口和 `.cmd` 启动脚本，执行语法编译和本地 HTTP 冒烟验证。

## Validation

- `python -m py_compile tokenflow.py` - 验证 Python 语法可编译。
- `python tokenflow.py --self-check` - 验证主路径、空输入校验、任务识别和 Token 指标。
- 启动本地服务后访问 `/health` 和 `POST /api/run` - 验证 HTTP 入口可用。

## Open questions

- 后续接入 Laya 或 Jev 时，需要再确定具体 SDK、模型版本、中文评测集和失败回退策略；这些不影响本版本地 MVP。

## Facts vs assumptions

- Verified: 案例文档要求本地工具、模型路由、上下文压缩、Token 统计和网页或桌面成品形态。
- Verified: 当前工作区此前没有实现代码或项目依赖。
- Assumed: 第一版采用标准库本地 Web 页面，先用估算 Token 和确定性压缩验证工作流闭环。
