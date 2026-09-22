# TokenFlow V2 Harness Execution

## Requirement reading

第二版在第一版本地文本压缩流程之后，增加显式的 Harness 执行层：将处理后的任务交给本机已有的 Codex、Claude Code 或 DSH，并根据任务类型和复杂度自动选择 Harness 与模型。

外部 Agent 只在用户点击执行按钮或调用 `POST /api/execute` 时启动；普通的 `POST /api/run` 继续只做本地处理。

## Reuse survey

- Reusing: `tokenflow.py` - 已有任务识别、内容压缩、Token 估算、HTTP 服务和页面。
- Reusing: Python standard library - `subprocess`、`shutil`、`os`、`time` 可覆盖进程启动、探测和超时控制。
- Verified local commands: Codex CLI 支持 `codex exec --model`；Claude Code 支持 `claude -p --model`；DSH 命令已安装但 profile 初始化当前遇到用户目录权限错误。
- Gap needing new code: Harness 探测、模型路由、无 shell 的命令构造、执行结果回传和安全页面入口。
- Existing tests: `tokenflow.py --self-check`。
- Build/test commands: `python -m py_compile tokenflow.py`；`python tokenflow.py --self-check`；启动后调用 `/api/harnesses` 和 `/api/execute`。

## Capability and file boundaries

- Harness 探测与模型路由：
  - `tokenflow.py` - 探测 `codex`、`claude`、`dsh`，按任务类型选择首选 Harness，按复杂度选择 fast/quality 模型。
- 安全命令执行：
  - `tokenflow.py` - 只允许白名单 Harness；使用 `subprocess.run(..., shell=False)`；固定当前工作区；限制请求体、输出长度和执行时间；默认使用 Codex read-only 与 Claude plan 模式。
- HTTP 与页面：
  - `tokenflow.py` - 增加 `GET /api/harnesses`、`POST /api/execute`，页面增加 Harness、模型选择和显式执行按钮。
- 说明：
  - `README.md` - 增加 V2 Harness 配置、模型别名和 DSH 权限边界说明。
- Unchanged: `start_tokenflow.cmd`、案例 DOCX 和第一版接口语义 - 启动方式和本地 `/api/run` 保持兼容。

## Interfaces and invariants

- `detect_harnesses() -> dict` - 返回白名单 Harness 是否安装、命令位置、默认模型和 DSH 配置提示；不启动 Agent 任务。
- `choose_harness(task_type, content) -> str` - 代码任务优先 Codex，文档/表格/通用任务优先 Claude；不可用时回退到其他已安装 Harness。
- `choose_model(harness, task_type, content) -> str` - 长文本或代码任务使用 quality，其余使用 fast；模型名可由请求覆盖。
- `POST /api/execute` - 接收 `{task, content, harness?, model?}`，先执行本地压缩，再调用选定 Harness，返回工作流指标和执行结果。
- `POST /api/run` - 保持纯本地处理，不启动外部进程。
- 只传递处理后的内容；不打印环境变量、密钥或完整用户环境。

## Build order

1. 增加 Harness 探测、模型选择和命令构造函数，用自检覆盖路由和安全参数。
2. 组装 `POST /api/execute`，验证错误、超时和输出截断路径。
3. 更新页面和 README，验证 `/api/harnesses`；只在用户显式请求时探测或执行本机 Harness。

## Validation

- Python 语法编译通过。
- 自检验证代码/文档路由、模型选择、只读/plan 参数和模型名校验。
- 本地 HTTP 验证 `/api/harnesses` 和 `/api/run` 保持可用。
- Harness 实际执行只在本机已配置且用户明确调用 `/api/execute` 时验证，避免无意消耗模型额度或修改文件。

## Open questions

- DSH 的 headless profile 实际接受的模型参数需要在 `DSH_HOME` 和 profile 权限可用后再确认；当前通过 `--model` 转发并在失败时返回原始错误。
- `gpt-5.6-sol`、`gpt-5.6-luna`、`v4f/pro` 是可配置模型别名，不在本地预先声称一定可用。

## Facts vs assumptions

- Verified: 当前机器存在 `dsh`、`codex` 和 `claude` 命令包装器。
- Verified: Codex 和 Claude CLI 帮助信息公开了非交互模式与模型参数。
- Verified: DSH 当前尝试创建用户目录 profile 时返回权限错误。
- Assumed: 当前工作区是默认 Harness 工作目录，第二版不开放任意路径执行。
