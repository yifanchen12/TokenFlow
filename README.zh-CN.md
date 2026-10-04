# TokenFlow

**面向 AI Agent 工作流的本地低 Token 执行框架。**

TokenFlow 在任务交给本地模型或 AI 编程 Harness 之前，先减少不必要的上下文。核心保持小而清晰：Python 标准库、确定性预处理、本地 HTTP 页面，以及可选的 FreeToken、Codex、Claude 和 DSH 适配器。

English documentation: [README.md](README.md)。

## 核心能力

- 将任务分类为代码、表格、文档或通用任务；
- 按任务匹配摘取文本，保留完整 Python 函数、Markdown 小节或正文段落；抽取范围过大或不确定时保留原文；
- 展示压缩前后的 Token 估算值；估算值不是计费级 tokenizer 结果；
- 检测本机 Codex、Claude、DSH 并选择默认 Harness/模型；
- 默认使用 Codex 只读模式和 Claude 计划模式；
- 通过 OpenAI 兼容接口连接本地 FreeToken，不复制其推理运行时；
- 支持可配置的 Ollama、Laya 兼容端点和云端 OpenAI 兼容端点；
- 支持 TXT、Markdown、源码、DOCX、XLSX，以及可选 PDF 文本提取；
- 使用 SQLite 保存文档分块和确定性的 64 维向量缓存；
- 通过显式接口调用 Jev 决策/路由能力，不把 Jev 当作普通聊天模型；
- 提供白名单 PC Agent 动作，默认 dry-run，执行需要显式授权；
- 提供需要用户确认的 Windows 安装器入口和 FreeToken 启动入口。
- 提供可选的 OmniRoute 安装、本机启动和仅存于进程内存的 Endpoint Key 配置入口。

## 架构

```text
浏览器或命令行
       |
       v
TokenFlow HTTP 服务 :8765
       |
       +--> 确定性预处理
       +--> FreeToken :1919（可选本地模型）
       +--> Codex / Claude / DSH（可选 Harness）
```

服务默认绑定 `127.0.0.1`。它不是认证网关，未增加认证和网络隔离前不应暴露到不可信网络。

## 快速开始

要求：Python 3.10 或更高版本。

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python tokenflow.py
```

然后打开 <http://127.0.0.1:8765>。Windows 用户也可以双击 `start_tokenflow.cmd`。

### Windows 可执行文件

可从 [GitHub 最新 Release 下载 Windows 可执行文件](https://github.com/yifanchen12/TokenFlow/releases/latest/download/TokenFlow.exe)，也可以执行 `build_tokenflow_exe.cmd` 自行生成 `dist/TokenFlow.exe`。程序会启动本地服务并在默认浏览器打开 TokenFlow 控制台；使用页面上的“关闭服务”按钮即可停止。二进制文件不提交到源码分支，而通过 Release 附件分发。

SQLite 缓存默认保存在当前用户的应用数据目录（Windows 示例：`%LOCALAPPDATA%\TokenFlow\tokenflow.db`）。设置 `TOKENFLOW_DB_PATH` 可指定其他位置。打开状态页不会创建数据库；首次索引文档时才会初始化。

如果在旧版工作目录或程序目录发现 `tokenflow.db`，缓存卡片会提供手动导入入口。文档会按内容去重并合并到当前缓存；旧数据库文件保持不变。

## FreeToken 集成

FreeToken 是可选组件。TokenFlow 使用其本地 OpenAI 兼容接口：

```text
GET  http://127.0.0.1:1919/health
GET  http://127.0.0.1:1919/v1/models
POST http://127.0.0.1:1919/v1/chat/completions
```

Windows 用户双击 `install_freetoken.cmd`。脚本依次查找 `TOKENFLOW_FREETOKEN_INSTALLER`、项目目录旁的安装包和用户 Downloads 目录。安装器在独立窗口打开，由用户明确确认。

设置 `TOKENFLOW_FREETOKEN_MODEL` 指向模型目录，然后在 TokenFlow 页面点击“启动 FreeToken”。脚本不会静默下载或执行安装器，本仓库不捆绑模型权重。

```powershell
$env:TOKENFLOW_FREETOKEN_MODEL = "<模型目录>"
$env:TOKENFLOW_FREETOKEN_URL = "http://127.0.0.1:1919"
python tokenflow.py
```

Linux 用户请按照 FreeToken 官方文档安装并启动服务。

## 提供商路由

`POST /api/chat` 支持 `provider: "auto" | "freetoken" | "ollama" | "omniroute" | "laya" | "cloud"`。自动路由遵循 `TOKENFLOW_PROVIDER_ORDER`，默认顺序为 `freetoken,ollama,laya,cloud`，但**只尝试 HTTP(S) 回环地址，且始终排除 `cloud` 和 `omniroute`**。即使 OmniRoute 运行在本机，也可能把内容转发给远程或付费模型，因此必须显式选择。远程 FreeToken、Ollama、Laya 端点同样不会进入自动路由。没有符合条件的端点时会报错。明确指定提供商才允许使用其远程地址；页面会在显式选择时再次确认，直接调用 API 的客户端须自行确认外发。

`POST /api/local-chat` 也只允许本机 FreeToken 地址；如果有意使用远程 FreeToken，请通过 `/api/chat` 明确指定 `provider: "freetoken"`。

通过环境变量配置：

```text
TOKENFLOW_FREETOKEN_URL       本地 FreeToken 地址，默认 http://127.0.0.1:1919
TOKENFLOW_OLLAMA_URL          Ollama OpenAI 兼容地址，默认 http://127.0.0.1:11434/v1
TOKENFLOW_OMNIROUTE_URL       OmniRoute /v1 地址，默认 http://127.0.0.1:20128/v1
TOKENFLOW_OMNIROUTE_API_KEY   OmniRoute 的 Endpoint Key；已测试的本机部署及远程网关均需提供
TOKENFLOW_OMNIROUTE_ALLOWED_MODELS  可选的明确模型 ID 白名单，逗号分隔；OmniRoute 始终要求明确模型
TOKENFLOW_LAYA_URL            用户自行提供的 Laya 兼容端点
TOKENFLOW_LAYA_API_KEY        可选 Laya 密钥，只从环境变量读取
TOKENFLOW_CLOUD_URL           用户自行提供的云端 OpenAI 兼容端点
TOKENFLOW_CLOUD_API_KEY       云端密钥，只从环境变量读取
TOKENFLOW_PROVIDER_ORDER      逗号分隔的提供商顺序
```

页面的“OmniRoute 下载与连接设置”会检测已安装的 CLI、启动默认本机网关；Windows 上还可在确认后打开可见窗口，执行上游包的 `npm install -g omniroute`。安装前须自行准备 Node.js/npm，并核对[上游项目](https://github.com/diegosouzapw/OmniRoute)和软件包来源；TokenFlow 不捆绑 OmniRoute，也不安装 Node.js。其他系统请按上游说明安装；已经安装的 CLI 不会重复安装。

在页面输入 `/v1` 地址和 Endpoint Key 后保存。页面输入的 Key 只留在当前 TokenFlow 进程的内存中（也可通过 `TOKENFLOW_OMNIROUTE_API_KEY` 提供），不会在接口响应中回显、写入磁盘或放进命令参数；关闭 TokenFlow 后该页面配置失效。Key 输入框留空表示保留现有 Key。地址不得包含凭据、查询或片段；远程网关必须使用 HTTPS 并提供 Key。“启动本地网关”只适用于默认回环地址，不管理自定义或远程网关。聊天接口调用 `/v1/models` 和 `/v1/chat/completions`。本仓库不猜测 Laya 的固定公网地址，请按实际部署配置。Jev 通过 `POST /api/jev/decision` 显式调用，使用 `JEV_API_KEY` 和可选的 `TOKENFLOW_JEV_URL`，不会被静默当作普通聊天模型调用。

OmniRoute 必须填写明确模型 ID（例如 `ollama-local/qwen3.5:9b`）；`auto` 会在发送内容前被拒绝。可在页面填写模型白名单并保存连接配置，或设置 `TOKENFLOW_OMNIROUTE_ALLOWED_MODELS`；白名单按完整 ID 匹配。留空允许明确指定的模型，不允许自动选择。同一规则适用于聊天、评测和 TokenFlow 启动的 Codex。`POST /api/omniroute/preview` 接受 `{"model":"<模型ID>"}`，只展示网关与所选模型，不发送任务或启动 Harness；页面会在执行前自动预览。TokenFlow 无法确认最终上游费用，也无法约束网关组合内部的回退，请在 OmniRoute 中核对这些设置。

## 文档解析与缓存

`POST /api/parse` 支持带本地 `path` 的 JSON 请求，也支持 `multipart/form-data` 文件上传。支持 TXT、Markdown、常见源码/文本文件、DOCX、XLSX；安装 `pypdf` 后支持 PDF 文本提取。

JSON 请求上限为 2 MB；上传文件上限为每个文件 20 MB。

`POST /api/index` 将提取文本保存到 SQLite，并生成确定性的 64 维哈希向量。中文使用连续二字/三字片段，英文使用单词；`POST /api/search` 在线性扫描时只保留得分最高的少量结果。旧向量会在新版首次索引或检索时事务性重建，原有文档保留；状态读取不会迁移或创建数据库。这是词法检索，不宣称等同于语义 Embedding 模型。

可选 PDF 解析依赖：

```powershell
python -m pip install pypdf
```

## PC Agent 安全边界

`POST /api/pc/plan` 只接受 `move`、`click`、`type`、`key`、`wait` 五类动作，并始终返回需要确认的 dry-run。`POST /api/pc/execute` 只有在显式设置 `TOKENFLOW_PC_AGENT_EXECUTE=1` 且安装可选依赖 `pyautogui` 后才会执行。

页面允许编辑 PC 动作 JSON 并预览校验后的计划；预览不会执行动作。`POST /api/shutdown` 用于停止本地服务，页面的“关闭服务”按钮会调用该接口。

系统不支持 Shell 动作、任意可执行文件动作或自动屏幕决策循环。启用执行前必须人工审查每个动作。

## 真实 Token 计数

TokenFlow 支持可选的本地 tokenizer 后端。未安装可选后端时，响应会明确标记为 `backend: "heuristic"` 和 `exact: false`。

使用本地 Hugging Face `tokenizer.json`：

```powershell
python -m pip install tokenizers
$env:TOKENFLOW_TOKENIZER_PATH = "<tokenizer目录或文件>"
```

使用 OpenAI 兼容编码：

```powershell
python -m pip install tiktoken
$env:TOKENFLOW_TOKENIZER_BACKEND = "tiktoken"
$env:TOKENFLOW_TIKTOKEN_ENCODING = "cl100k_base"
```

通过 `GET /api/tokenizer` 查看当前后端。Tokenizer 只从本地加载，TokenFlow 不会自动下载模型文件。

### 收益统计与质量评测

`/api/run` 估算正文 Token；模型和 Harness 接口统计实际使用的可见提示词文本。长内容按任务匹配，保留完整 Python 函数、Markdown 小节或正文段落。总结、翻译等广覆盖任务、无匹配片段、抽取范围超过限制或无估算收益时发送原文，并返回 `compression_applied: false`。这种启发式方法不保证所有相关事实都被找到。`token_counting.scope` 标明统计范围；数字不包含隐藏开销，不等同于账单用量。

运行内置的三类示例基准，不会连接模型或上传内容：

```powershell
python eval_workflow.py
```

需要比较同一模型对原文和候选压缩文本的答案时，明确指定已配置的提供商；这会向该端点发送两份提示词：

```powershell
python eval_workflow.py --provider freetoken --model <已安装模型名>
```

仓库还提供了 60 个基于实际项目代码和公开文档的可复现问答任务。它们属于项目问答基线，不是实际用户任务日志：

```powershell
python eval_workflow.py --cases eval_cases.repository.json --report tmp/offline.json
python eval_workflow.py --cases eval_cases.repository.json --provider ollama --model <已安装模型名> --report tmp/model-comparison.json
```

本机服务使用自定义端口时设置 `TOKENFLOW_OLLAMA_URL`。评测比较原文与 TokenFlow 实际选中的提示词，记录答案、响应中的 usage 和耗时；交替执行顺序，分别统计文本估算与提供商报告的输入/输出总量。每个任务完成后生成 JSON 和 Markdown；报告可能含任务答案，分享前须检查。

Ollama 评测使用原生聊天 API，设置 `--ollama-context`（默认 32768）、温度 0 和关闭思考，并记录设置；原文和处理后提示词使用相同参数。请为原文和输出预留足够上下文容量；普通应用聊天仍使用原有 OpenAI 兼容接口。这遵循 [Ollama 的上下文说明](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size)。

自有样本通过 `--cases` 提供，每项需要 `task`、`content`（或样本文件所在目录内的相对 `source_file`）及 `must_keep` 关键原文数组。模型评测还需要 `expected_answer` 字符串或可接受答案数组。默认 `answer_match: "exact"` 精确匹配；显式使用 `contains_all` 时要求全部片段存在，不会把 `17` 中的 `7` 判为正确。请准确标注 `source_kind`。

默认质量门槛要求至少 50 个完整对比、标注事实零丢失、处理后准确率至少 90%、答案准确率下降不超过 2 个百分点，且提供商报告的输入 Token 至少节省 5%。缺少 usage 或样本未完成时标记 `not_evaluated`，不会算通过。`--enforce-gate` 会在门槛未通过时以退出码 2 结束，阈值可调整。通过只针对所提供样本，不证明普遍质量或账单收益；已测结果与限制见[项目问答对比报告](docs/reports/repository-quality.md)。

## Harness 执行

页面可以把处理后的任务交给 Codex、Claude 或 DSH；无估算收益时保留原文。代码任务优先 Codex，其它任务优先 Claude；长文本和代码任务优先 quality 模型。

```powershell
$body = @{task="分析这段代码"; content="print('hello')"; harness="auto"; model="auto"} | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8765/api/session
$headers = @{"X-TokenFlow-Token"=$session.token}
Invoke-RestMethod http://127.0.0.1:8765/api/execute `
  -Method Post -ContentType "application/json" -Headers $headers -Body $body
```

默认 Codex 使用只读模式，Claude 使用计划模式。DSH 需要配置 `DSH_HOME` 和 profile。

若想让 TokenFlow 启动的 Codex CLI 走 OmniRoute，在 `/api/execute` 指定 `harness: "codex"`、`harness_backend: "omniroute"` 和白名单允许的明确 `model`，或使用页面选项；`auto` 会被拒绝。默认后端仍为 `direct`。OmniRoute 模式使用本次调用的 `/v1/responses` 参数，不修改全局 Codex 文件。上游账号、费用和回退由网关决定，网关须已启动并支持 Responses API；这不是 Codex 桌面应用的全局设置。API 客户端应自行取得外发与可能计费的授权。

## HTTP 接口

```text
GET  /health
GET  /api/harnesses
GET  /api/freetoken
GET  /api/omniroute
GET  /api/providers
GET  /api/tokenizer
GET  /api/store
GET  /api/session
GET  /api/local-models
POST /api/shutdown
POST /api/run
POST /api/local-chat
POST /api/chat
POST /api/execute
POST /api/parse
POST /api/index
POST /api/store/migrate
POST /api/search
POST /api/pc/plan
POST /api/pc/execute
POST /api/jev/decision
POST /api/freetoken/install
POST /api/freetoken/start
POST /api/omniroute/config
POST /api/omniroute/install
POST /api/omniroute/start
POST /api/omniroute/preview
```

所有 POST 请求都必须携带 `GET /api/session` 返回的进程级令牌；浏览器请求还必须来自匹配的本机 Origin。OmniRoute 设置接口额外要求请求来自回环地址。该令牌用于降低跨站请求风险，不是用户身份认证。安装和启动接口可以控制本地进程，不要将它们转发到公网反向代理。

## 安全与隐私

部署前请阅读 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。保持服务绑定 `127.0.0.1`，绝不提交密钥或个人路径，将 Harness 执行视为代码执行，并从官方来源获取第三方运行时和模型。

## 开发

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python eval_workflow.py --self-check
```

GitHub Actions 会在 Windows 自动自检、构建 EXE，通过 `scripts/smoke_exe.ps1` 验证启动、状态、预览和关闭，并提供 EXE 与 SHA256 构建附件。正式 Release 单独上传已验证的程序和校验文件，不把二进制提交到源码分支。

提交 Pull Request 前，请检查 diff 中是否包含密钥、绝对路径、日志、模型文件、凭据或未经审查的子进程行为。详见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。

## 当前边界

- Token 数可能是启发式估算；即使使用可选 tokenizer，可见提示词估算也不是账单用量；
- 预处理是确定性、保留结构的抽取，不是语义摘要；真实业务代表性的质量仍需外部任务验证；
- 本地 HTTP 接口没有用户认证、配额和多用户隔离；进程级写令牌仅用于降低浏览器跨站请求风险；
- TokenFlow 不分发 FreeToken、OmniRoute 运行时、模型权重或第三方 Harness；
- 提供商是否可用取决于用户配置和网络条件；
- PDF 提取需要可选 `pypdf`，本地模型接入不代表仓库包含模型权重；
- 哈希向量缓存是确定性检索基础设施，不是训练得到的 Embedding 模型；
- PC Agent 执行需要显式环境开关，并且仍要求人工审查。

## 许可证

采用 MIT 许可证发布，详见 [LICENSE](LICENSE)。
