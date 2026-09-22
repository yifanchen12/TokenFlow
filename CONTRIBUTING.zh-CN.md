# 贡献指南

感谢参与 TokenFlow。请保持修改小而清晰，便于审查，并确保本地执行安全。

English guide: [CONTRIBUTING.md](CONTRIBUTING.md)。

## 开始前

- 阅读 [README.zh-CN.md](README.zh-CN.md) 和 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)；
- 不要包含 API Key、Cookie、凭据、个人路径、模型权重、安装器、日志或虚拟环境；
- 安全问题按照 `SECURITY.zh-CN.md` 的私下报告流程处理，不要创建公开 Issue。

## 开发检查

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
git diff --check
```

每个 Pull Request 前都应运行这些检查。如果修改涉及子进程执行、HTTP 路由、安装器发现或路径处理，请增加针对性测试或说明人工验证过程。

## Pull Request 规范

- 说明用户可见行为和安全影响；
- 优先使用 Python 标准库和最小可行实现；
- 保持本地进程执行显式，避免 Shell 字符串拼接；
- 保持回环绑定、Harness 只读/计划模式和不静默执行安装器；
- 行为变化时同步更新文档；
- 保持提交聚焦并使用清晰的提交信息，例如 `feat: add FreeToken lifecycle controls`。

## 许可证

提交贡献即表示你同意该贡献按照本仓库的 MIT 许可证提供。
