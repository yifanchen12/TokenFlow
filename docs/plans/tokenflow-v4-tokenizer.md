# TokenFlow V4 Tokenizer 计数阶段计划

## 目标

将 MVP 中的字符长度估算升级为可验证的 tokenizer 计数，同时保留无依赖环境下的明确回退，不把启发式结果误报为真实 Token 数。

## 已实现

- 新增 `token_counter.py`；
- 支持本地 `tokenizers` 读取 `tokenizer.json`；
- 支持本地 `transformers` Fast tokenizer 目录；
- 支持可选 `tiktoken` 编码；
- 所有结果返回 `backend`、`exact` 和 `tokenizer` 元数据；
- 新增 `GET /api/tokenizer`；
- 无可用后端时返回 `heuristic`，不访问网络；
- `--self-check` 和 Windows 编译脚本覆盖新模块。

## 配置约定

- `TOKENFLOW_TOKENIZER_PATH`：本地 tokenizer 目录或 `tokenizer.json` 文件；
- `TOKENFLOW_TOKENIZER_BACKEND`：可设为 `auto` 或 `tiktoken`；
- `TOKENFLOW_TIKTOKEN_ENCODING`：默认 `cl100k_base`。

## 验收标准

- 默认无可选依赖时：`exact=false`，响应明确标注 `heuristic`；
- 配置可用本地 tokenizer 后：`exact=true`，计数由真实 tokenizer 返回；
- 不因 tokenizer 不可用而阻塞本地压缩、Harness 或 FreeToken 流程；
- 不自动下载模型或 tokenizer 文件；
- 不提交 tokenizer、模型权重或本机路径。

## 后续

下一阶段实现 DOCX、PDF、Excel 的统一抽取接口，并让抽取结果进入同一 Token 计数和缓存流程。
