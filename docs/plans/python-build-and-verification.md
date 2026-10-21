# Python 项目构建、修复与证据验收

状态：已被用户的目标澄清替代，暂停此专用构建方案。2026-10-20。

用户澄清本轮目标为优化产品体验和分配，让端云协同处理复杂任务。澄清时尚未修改实现代码；后续以 `edge-cloud-task-experience.md` 的确认范围为准。本文件保留讨论记录，不作为当前实现依据。

## Requirement reading

用户已确认优先支持 Python 项目构建、修复与测试。目标是在指定项目上自动完成检查、测试和构建，失败时有限修复，最终结论可由实际进程结果和产物复核。

首版范围：本机 Python 项目；支持 unittest / pytest 测试；支持语法与测试检查，以及通过已安装的 PyInstaller 构建 EXE。自动修复最多两轮，只在工作副本内修改普通 Python 源文件。原项目不自动回写。wheel、依赖自动安装、跨语言构建和任意 shell 命令生成不在首版范围。

“可信”不使用模型评分或百分比表达。语法、测试、构建、产物及运行检查分别报告验证状态；测试通过只证明现有测试覆盖的行为。

## Reuse survey

- Reusing: `tokenflow.py:_run_bounded` — 已支持参数数组、`shell=False`、有界日志、超时、取消及进程树终止。构建与测试复用此执行能力；需要把现有 Harness 专用错误文案泛化为调用方可解释的执行结果。
- Reusing: `task_orchestrator.py:TaskRun`、`start_task`、`cancel_task` — 复用任务队列、阶段进度、持久化、幂等提交、调用预算和取消机制。
- Reusing: `TaskRun.call` / `role_call` — 修复建议使用已配置的执行模型。模型只返回结构化源文件修改建议，不决定编译命令或最终通过状态。
- Reusing: `runtime_config.py:RuntimeConfig` — 现有任务记录可保存构建证据，无需新数据库。编译任务没有可比较的原文回答基线，累计费用节省不得凭空增加。
- Reusing: `ui_page.py` 和 `scripts/test_ui.js` — 复用任务进度、轮询恢复、取消、历史与结果区域。
- Standard library: `pathlib`、`shutil`、`tempfile`、`ast` / `py_compile`、`unittest`、`json`、`xml.etree.ElementTree`。pytest 的 JUnit XML 用于读取真实测试数量与失败结果。
- Verified installed: Python 3.14.7；PyInstaller、pytest、build、setuptools 可导入。目标项目可以使用另一套明确指定的 Python / 虚拟环境；必须重新预检，不能假定当前解释器就是目标项目环境。
- Gap needing new code: 目标项目与解释器选择、工作副本、确定性检查与构建、受限修复、分项证据及产物交付。目前没有这些能力。
- Current limitation: Codex Harness 使用 `read-only`，Claude 使用 `plan`，当前原生执行路径依赖 `os.getcwd()`。本方案保留其权限与连接方式；通过提供代码与日志获得修复建议，不需要全局开放写权限。
- Existing tests: `test_orchestration.py`、`test_controls.py`、`test_savings.py`、`scripts/test_ui.js`。
- Existing checks: `python tokenflow.py --self-check`；`python eval_workflow.py --self-check`；`node scripts/test_ui.js`；模块语法检查；`scripts/smoke_exe.ps1`；`build_tokenflow_exe.cmd` 使用 PyInstaller 打包。

## Capability and file boundaries

- 项目检查、构建与修复：
  - Add `python_build.py` — 统一负责请求校验、项目副本、Python 环境预检、语法检查、测试、PyInstaller 构建、修复建议校验与应用、产物和证据报告。先作为独立模块验证，再接入任务编排。
  - Add `test_python_build.py` — 使用临时 Python 小项目验证真实成功、失败及修复结果；覆盖不能错误标为成功的关键分支。
- 接入已有任务生命周期：
  - Modify `task_orchestrator.py` — 显式 `python_build` 模式；复用运行锁、请求回执、模型调用预算和取消；构建任务走确定性验收，不经过文字回答的模型验收器。
  - Modify `tokenflow.py` — 提供环境预检和受保护的证据/产物下载入口；复用现有任务启动入口。共享有界执行器仅按必要范围调整，保持原 Harness 调用兼容。
  - Modify `runtime_config.py` only if required — 区分构建任务的费用统计口径，修复调用正常记账，但不虚构“节省”。不新建任务数据库。
- 界面、文档与打包：
  - Modify `ui_page.py` — 增加 Python 构建入口及项目、解释器、测试框架、入口文件等必要字段；复用进度条；呈现实际命令、退出码、测试数量、修复轮次、日志和产物路径/下载。
  - Modify `scripts/test_ui.js`、`scripts/smoke_exe.ps1` — 检查新模式、证据显示和 EXE 中调用外部 Python 的主路径。
  - Modify `build_tokenflow_exe.cmd` — 纳入新模块语法检查和必要打包内容。
  - Modify `README.md` / `README.zh-CN.md` — 使用方式、依赖前提、结果含义和能力边界。
- Unchanged: 文档解析、OCR、查表、统计、评测样本及 PC Agent。Harness 的默认权限、已有连接和模型选择保持现状。

## Interfaces and invariants

- `POST /api/tasks/start` 增加显式 `mode: "python_build"`；普通任务不传该字段时保持现有行为。构建配置包含绝对项目目录、外部 Python 路径、测试框架及目录、构建模式（仅检查 / PyInstaller）、EXE 入口文件、最多修复轮数。配置必须参与幂等请求指纹。
- 预检返回 Python 版本、解释器可用性、所需测试/打包模块可用性和可执行的步骤。EXE 内不能将自身 `sys.executable` 当成 Python 解释器。
- 副本保留源码与测试，排除 `.git`、虚拟环境、缓存及旧构建产物。拒绝越出项目的符号链接 / junction，设置副本规模上限并给出明确错误。依赖仍来自用户指定解释器，不复制整个虚拟环境。
- 构建使用固定的 Python 模块调用及参数数组，不由模型生成 shell 命令。缺少依赖报告受阻，不自动安装或下载。
- 修复接口返回 `edits`，每项包含项目内相对 `.py` 路径、待替换原文和新文本。只允许修改本轮明确提供给模型的普通源码；越界、重复/歧义替换、测试文件、构建配置及空洞结果均拒绝。受限上下文不足时停止自动修复，不猜测未读取的文件。
- 修复不能修改测试、测试选择、断言、验收器或构建规则；每轮重新进行语法和同一套测试。日志使用失败输出及相关源码，敏感配置不送入模型；进程日志在存储/展示前脱敏，不继承模型连接密钥用于构建子进程。
- 构建输出来自本次新工作目录；只有构建命令退出为 0 且本次预期产物存在且非空，才显示构建通过。保留产物大小和 SHA256，方便交付核对。
- 证据包含每一步的实际命令（不含密钥）、目录、起止时间、退出码、超时/取消标记、日志截断标记；测试数量、失败、错误、跳过分别记录。无法读取完整测试结果不能标为测试通过。
- 零测试、测试失败、缺少依赖、超时、取消、输出异常或缺失产物都不能显示全部通过。仅检查模式不显示“已生成 EXE”。无冒烟检查时明确标注“运行未验证”；提供明确运行参数和预期输出后，才执行有超时的产物冒烟检查。
- 不声称工作副本是系统沙箱。构建和测试会执行用户代码，首版适用于用户指定且可信的本机项目，使用当前用户权限。副本和受限修改避免自动覆盖原项目，但不构成对恶意项目的系统隔离。
- 工作副本、修复差异、报告和产物可查看；原项目不自动回写。下载入口只提供该任务登记的报告/产物，不能接受任意本地文件路径。

## Build order

1. 独立验证 `python_build.py`：真实临时项目完成预检、语法、测试与构建；先验证不依赖模型的证据判定。
2. 验证受限修复模块：使用确定性修复响应确认正确修复会重新运行同一测试；越界及篡改验收的建议被拒绝；再接入已有执行模型调用和预算。
3. 与 TaskRun 组装：验证进度、取消、幂等、历史恢复和终态；确定性结果直接决定验收，模型不能覆盖失败证据。
4. 接入页面和下载：桌面及窄屏操作检查，明确区分各项通过/失败/未验证。
5. 完成仓库自检并打包新 EXE，使用打包后的程序调用外部 Python 跑一份已知项目，复核报告与实际产物。

## Validation

- 正常小项目：至少一个真实测试通过；PyInstaller 构建出新产物；带已知参数的控制台程序冒烟输出符合预期。
- 可修复项目：语法错误或逻辑错误引起初次检查失败；修复后重新运行原测试并通过，保留失败及修复后的两组证据。
- 不可误报：零测试、失败测试、缺依赖、退出为 0 但无产物、截断/损坏的测试报告均不标为全部通过。
- 边界：模型试图改测试或越界时拒绝；超时/取消终止进程树；连续提交相同请求不重复构建；原项目源码不变。
- 真实模型验证与确定性夹具分开记录。若本地模型无法修复，报告实际失败，不用模拟响应替代真实模型效果。
- 回归：主程序和评测自检、UI 检查、语法检查、EXE 冒烟、`git diff --check`。

## Open questions

- 用户暂未提供实际项目。先用带人工确定答案的临时小项目验证能力；不据此宣称已验证任意真实工程。之后可对用户项目补做验收。

## Facts vs assumptions

- Verified: 用户选择优先支持 Python 项目；当前工具存在有界执行、调用预算、任务持久化和进度界面；当前环境有 Python、pytest 和 PyInstaller。
- Assumed for approval: 首版以 unittest / pytest 和 PyInstaller EXE 为范围；采用副本交付修复，不直接修改原项目；自动修复最多两轮。用户确认后按此边界实施。
