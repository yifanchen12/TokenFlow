# TokenFlow V3 FreeToken Provider

## Requirement reading

将 FreeToken 接入 TokenFlow，作为本地模型推理后端。TokenFlow 负责任务压缩、模型选择和回退；FreeToken 负责本地模型服务，不将其 CUDA/MoE 内核复制进本项目。

本版本只实现 HTTP 接口，不自动安装 FreeToken、CUDA、WSL 或模型权重。

## Reuse survey

- Reusing: `tokenflow.py` - 已有本地压缩、任务识别、Harness 路由和 Web API。
- Reusing: Python standard library - `urllib.request`、`json`、`threading` 可实现无第三方依赖的 OpenAI 兼容客户端和模拟服务测试。
- Verified upstream contract: FreeToken 提供 `ft serve`，默认 HTTP 服务端口为 1919，并提供 `/v1/models`、OpenAI `/v1/*` 和 Anthropic `/v1/messages` 兼容接口。
- Gap needing new code: FreeToken 服务探测、模型列表读取、聊天请求、超时处理和本地模型回退。
- Existing tests: `tokenflow.py --self-check`。
- Build/test commands: `python -m py_compile tokenflow.py freetoken_provider.py`；`python tokenflow.py --self-check`。

## Capability and file boundaries

- FreeToken HTTP 客户端：
  - `freetoken_provider.py` - 封装健康检查、模型列表和 OpenAI 兼容聊天请求；只传递 JSON，不使用 shell，不读取密钥。
- 本地模型路由：
  - `tokenflow.py` - 增加 `/api/local-models` 和 `/api/local-chat`，在任务压缩后选择 FreeToken 模型；服务不可用时返回明确状态，不隐式调用云端。
- 页面展示：
  - `tokenflow.py` - 增加 FreeToken 地址、模型检测和本地模型执行入口。
- 使用说明：
  - `README.md` - 增加 FreeToken 启动、模型和 Windows/WSL 注意事项。
- Unchanged: `start_tokenflow.cmd` - TokenFlow 仍可独立启动；FreeToken 是可选后端。

## Interfaces and invariants

- `FreeTokenClient(base_url).health() -> dict` - 检查 `/health`；失败返回结构化错误而不是抛出未处理异常。
- `FreeTokenClient(base_url).list_models() -> dict` - 请求 `/v1/models`，返回服务端模型数据。
- `FreeTokenClient(base_url).chat(model, messages) -> dict` - 请求 `/v1/chat/completions`；限制请求和响应大小，支持明确超时。
- `POST /api/local-chat` - 接收 `{task, content, model?}`，先走本地 TokenFlow 压缩，再请求 FreeToken；缺少模型时从 `/v1/models` 选择第一个可用模型。
- `GET /api/local-models` - 返回 FreeToken 健康状态、地址和模型列表。
- 默认地址为 `http://127.0.0.1:1919`，可由 `TOKENFLOW_FREETOKEN_URL` 覆盖。
- FreeToken 不可用时不能伪造“本地模型执行成功”，必须返回 `available: false` 和错误原因。

## Build order

1. 实现 `FreeTokenClient` 和模型选择纯函数，用本地模拟 HTTP 服务验证协议。
2. 组装 TokenFlow 的本地模型 API，与现有本地压缩流程连接。
3. 更新页面与 README，执行语法编译、自检和接口冒烟测试。

## Validation

- 编译 `tokenflow.py` 和 `freetoken_provider.py`。
- 自检覆盖 URL 规范化、模型选择、错误返回和 OpenAI 响应解析。
- 模拟 FreeToken 服务验证 `/api/local-models` 和 `/api/local-chat`。
- 真实 FreeToken 验证留给用户安装并启动本地服务后进行，不自动下载模型或消耗显存。

## Open questions

- 具体默认模型要根据 `/v1/models` 实际返回和 8 GB 显存条件选择，不能把 DeepSeek-V4 等大模型视为当前机器必然可运行。
- FreeToken 的真实端到端延迟、上下文上限和工具调用能力需要在本地服务启动后实测。

## Facts vs assumptions

- Verified: FreeToken 官方文档提供 OpenAI 兼容接口和 `ft serve`。
- Verified: 当前工作区没有 `ft` 命令，WSL 状态检查受权限限制。
- Assumed: TokenFlow 先使用 OpenAI 兼容 `/v1/chat/completions`，后续再按需要接入 Anthropic 原生协议。
