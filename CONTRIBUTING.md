# 贡献指南

感谢你对 jiujiu-bookstack 的兴趣！🎉

## 如何贡献

### 报告 Bug

在 GitHub Issues 提 bug 时，请包含：
- 复现步骤
- 预期行为 vs 实际行为
- Python 版本 / OS / PostgreSQL 版本
- 完整报错信息（用 ``` 包裹）

### 提交代码

1. Fork 仓库
2. 创建 feature 分支：`git checkout -b feature/my-feature`
3. 提交代码：遵循下方的代码规范
4. 跑测试：`pytest tests/`
5. 提 PR：描述改动 + 关联 issue

### 新增功能

开 PR 前最好先开 issue 讨论，避免重复劳动。

## 代码规范

### 风格

- 用 `ruff check .` 检查
- 函数/变量名：`snake_case`
- 类名：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`
- 私有函数前缀：`_func_name`

### 文档字符串

每个公开函数都要有 docstring：

```python
def generate_skill(book_id: int, llm: LLMClient) -> Path:
    """生成 SKILL.md

    Args:
        book_id: 书的 ID
        llm: LLM 客户端

    Returns:
        SKILL.md 文件路径
    """
```

### 测试

- 每个新功能必须有测试
- 测试放 `tests/` 目录
- 测试函数名：`test_xxx`

### 数据流闭环

**重要原则**：所有下游产物（script/summary）必须能引用上游（mindmap/skill）。

如果你新增一个"步骤 X"，需要明确：
1. X 引用谁？
2. 谁引用 X？

## 项目结构

| 路径 | 用途 |
|------|------|
| `scripts/` | 核心 pipeline 脚本 |
| `agent/` | DeepAgent 剧本杀交互 |
| `config/` | 配置文件（含 schema、词库、分类规则） |
| `docs/` | 用户文档 |
| `tests/` | 单元测试 |
| `examples/` | 真实运行产物示例 |
| `.github/workflows/` | GitHub Actions CI |

## 提交规范

用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新功能
fix: 修复 bug
docs: 文档改动
refactor: 重构
test: 加测试
chore: 杂项（CI / 依赖等）
```

示例：
```
feat: 增加 TTS 音色配置项
fix: 修复 summary 重复生成时的缓存短路
docs: 更新 README 安装步骤
```

## 发布流程

1. 更新 `CHANGELOG.md`
2. bump version（`git tag v0.x.y`）
3. GitHub Actions 自动发 release

## 行为准则

- 友善、尊重、不带人身攻击
- 欢迎新手提问（用 issue 而不是私下问）
- 反馈批评时给出具体建议

## 联系方式

- GitHub Issues: https://github.com/fuermos/jiujiu-bookstack/issues
- 公众号文章: https://github.com/fuermos/jiujiu-bookstack#衍生文章
