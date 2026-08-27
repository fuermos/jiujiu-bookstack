# TDD 回归测试文档 (Test-Driven Development)

> **主人 2026-08-24 钦定**：按 TDD 流程做回归测试 — 先写测试用例（Red）→ 修代码（Green）→ 重构 + 验证（Refactor）。
> 每次修复完必跑 `pytest tests/ -v` 确认无回归。

## 🎯 测试哲学

```
RED:    写一个失败的测试 → 它精确地描述了当前 bug 的症状
GREEN:  写最小代码让它通过 → 不多做
REFACTOR: 清理代码 + 测试代码 → 让两者都更清晰
```

**核心规则**：**修复 bug 之前先写测试**。如果 bug 没有测试用例 → 它就在逃过 TDD 网 → 早晚复发。

## 📁 测试目录结构

```
tests/
├── TDD_README.md                  # 本文档 (方法论 + 工作流)
├── test_regression_bugs.py        # 🆕 主人 2026-08-24 钦定: 回归测试套件
├── test_config_loader.py          # 已存在
├── test_llm_client.py             # 已存在
└── test_text_sanitize.py          # 已存在
```

## 🆕 `test_regression_bugs.py` 覆盖的 5 大场景

| # | 测试函数 | 验证什么 | Bug 例子 |
|---|---------|---------|---------|
| 1 | `test_no_cross_book_contamination` | 每本书剧本里的角色/术语不能串场 | 数学书出现"华生" |
| 2 | `test_holmes_specific_characters` | 福尔摩斯剧本**必须**用福尔摩斯角色 | 不能用别的书的角色 |
| 3 | `test_single_script_book_routing` | 单剧本的书走智能跳过流程 | modal 三步流必须工作 |
| 4 | `test_token_persistence` | 登录 token 跨 session 持久化 | query_params + 文件存储 |
| 5 | `test_mcp_list_books_no_duplicates` | MCP list_books 必须去重 | 不许 JOIN 产生重复行 |

每个测试都包含：
- **Given**（前置条件）
- **When**（执行动作）
- **Then**（期望结果）
- **Tag** 标记 bug 来源（`@pytest.mark.bug_2026_08_24`）

## 🚀 工作流

#### 1. 发现 bug
主人截图 / F12 Network / 实际使用中发现。

#### 2. 写测试（Red）
```bash
# 在 tests/test_regression_bugs.py 加一个 test_xxx 函数
# 故意让它跑失败 (断言现状 = bug 存在)
pytest tests/test_regression_bugs.py::test_xxx -v
# 应该看到 FAILED
```

#### 3. 修代码（Green）
```bash
# 在 web/app.py 或 scripts/*.py 里改
# 再跑测试
pytest tests/test_regression_bugs.py::test_xxx -v
# 应该看到 PASSED
```

#### 4. 回归（Refactor）
```bash
# 跑全部测试, 确保没碰坏别的
pytest tests/ -v
# 全部 PASSED 才算完事
```

#### 5. commit
```bash
git add tests/ web/app.py scripts/*.py
git commit -m "fix(area): bug描述 + 引用回归测试 test_xxx"
```

## 📝 写测试的最佳实践

### ✅ DO
- **每个测试只测一件事**（多个断言要相关）
- **测试名要描述行为**：`test_no_cross_book_contamination` 比 `test_bug_1` 好
- **Bug 名字带日期 tag**：`@pytest.mark.bug_2026_08_24`
- **修复 bug 时引用测试**：commit message 写 `fix: ... (test_xxx)`
- **fixture 复用**：`db_pool`, `mcp_session` 等共享 fixture 提到 conftest.py

### ❌ DON'T
- **不要"快速验证后删除测试"** — bug 复发时这是救命稻草
- **不要 mock 掉核心路径** — 单元测试 ≠ 集成测试
- **不要把 LLM 调用写进测试**（除非 mock） — 太慢 + 不可重现
- **不要 hardcode book_id** — 用 name 查询后拿 id

## 🐛 已修复的 Bug 回归清单

| Date | Bug | Test | Status |
|------|-----|------|--------|
| 2026-08-24 | 数学书被注入 Holmes 角色 | `test_no_cross_book_contamination` | ✅ 修 |
| 2026-08-24 | Holmes 书 modal 显示 2 次 (list_books JOIN 重复) | `test_mcp_list_books_no_duplicates` | ✅ 修 |
| 2026-08-24 | 登录刷新掉登录 | `test_token_persistence` | ✅ 修 |
| 2026-08-24 | modal 一锅端（不三步） | `test_single_script_book_routing` | ✅ 修 |
| 2026-08-24 | 宿主机 `python` 命令不存在 (streamlit 抛 Errno 2) | (本次未加测试 — 下次补) | ✅ 修 |

## 🛠️ 如何跑测试

```bash
# 单个测试
pytest tests/test_regression_bugs.py::test_no_cross_book_contamination -v

# 全部
pytest tests/ -v

# 只跑 regression 类
pytest tests/test_regression_bugs.py -v -m regression

# 看覆盖率 (需要 pytest-cov)
pytest tests/ --cov=scripts --cov=web --cov-report=term-missing
```

## 🔧 TDD 实战: 修新 bug 的步骤

1. **观察**：主人说「从福尔摩斯剧本进去着呢吗也是神奇的数学」
2. **复现**：写 `test_xxx_holmes_doesnt_show_math` 跑一次 → RED
3. **诊断**：看代码 → 发现 `script 32 (ch=56-66) 污染了"数学"字符`
4. **写最小修法**：`enrich_script_for_immersive` 标记或重生成
5. **修完跑测试** → GREEN
6. **跑全套**确认没碰坏别的
7. **commit**

## 📚 参考

- [pytest 官方文档](https://docs.pytest.org/)
- TDD 三原则：Red → Green → Refactor (Kent Beck)
- 项目内部：每个 `web/app.py` 修改必须配 `tests/test_regression_bugs.py` 同步更新

---

**记住**：TDD 不是仪式，是「写测试 → 改代码 → 再跑测试」的循环。每一次循环 = 一次回归测试的机会。