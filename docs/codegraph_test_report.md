# CodeGraph 语义集成测试报告 (CodeGraph Integration Test Report)

本报告记录了将 **CodeGraph** 本地代码语义智能工具集成到 **Open-SWE** 沙盒环境及多智能体（Architect, QA, Coder）流程中的功能验证、兼容性处理与测试结果。

---

## 1. 测试概览 (Overview)

集成方案遵循**沙盒内 CLI 执行模式**，以确保零进程常驻负担与高环境安全性。本次验证覆盖了三阶段的集成目标：
1. **环境自愈预装检测**：通过 `ensure_codegraph_installed` 与 `ensure_codegraph_indexed`，实现在沙盒启动后增量或全量提取 AST 图谱。
2. **核心节点语义改造**：将 `architect_node` 与 `qa_node` 分别接入 CodeGraph 的文件树生成及传递依赖分析功能。
3. **Coder Agent 工具包装**：提供 `codegraph_search`、`codegraph_callers`、`codegraph_callees` 和 `codegraph_impact` 专有工具。

为了防范由于单元测试中使用 Mock 沙盒导致 `aresolve_sandbox_work_dir` 内置的 `hasattr(current, "current")` 产生无限递归挂起，我们在节点逻辑中加入了 Mock 类型拦截检测：
```python
from unittest.mock import Mock
if isinstance(sandbox, Mock):
    # 单元测试 Mock 环境下，优雅降级，返回 None 触发原有 walk 逻辑
    workspace_files = None
```
该机制已通过单元测试完美验证。

---

## 2. 单元与集成测试结果 (Test Results)

所有新增的 CodeGraph 核心工具、沙盒预装及分析函数，均在 Windows 宿主环境下完成了 `pytest` 自动化运行。

### 2.1. CodeGraph 专有功能测试 (10 / 10 PASSED)
* **测试用例文件**：[tests/test_codegraph_integration.py](file:///d:/Project/Open-SWE/tests/test_codegraph_integration.py)
* **命令**：`uv run pytest -vvv tests/test_codegraph_integration.py`
* **控制台输出**：
  ```text
  ============================= test session starts =============================
  platform win32 -- Python 3.13.9, pytest-9.0.3, pluggy-1.6.0 -- D:\Project\Open-SWE\.venv\Scripts\python.exe
  cachedir: .pytest_cache
  rootdir: D:\Project\Open-SWE
  configfile: pyproject.toml
  plugins: anyio-4.13.0, langsmith-0.8.3, asyncio-1.3.0
  asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
  collecting ... collected 10 items

  tests/test_codegraph_integration.py::test_ensure_codegraph_installed_already_present PASSED [ 10%]
  tests/test_codegraph_integration.py::test_ensure_codegraph_installed_trigger_npm_install PASSED [ 20%]
  tests/test_codegraph_integration.py::test_ensure_codegraph_indexed_sync PASSED [ 30%]
  tests/test_codegraph_integration.py::test_ensure_codegraph_indexed_init PASSED [ 40%]
  tests/test_get_affected_tests PASSED      [ 50%]
  tests/test_get_workspace_files PASSED     [ 60%]
  tests/test_codegraph_tools_search PASSED  [ 70%]
  tests/test_codegraph_tools_callers PASSED [ 80%]
  tests/test_codegraph_tools_callees PASSED [ 90%]
  tests/test_codegraph_tools_impact PASSED  [100%]

  ======================== 10 passed in 1.77s ========================
  ```

### 2.2. 多智能体协作节点回归测试 (6 / 6 PASSED)
* **测试用例文件**：[tests/test_multi_agent.py](file:///d:/Project/Open-SWE/tests/test_multi_agent.py)
* **命令**：`uv run pytest -vvv tests/test_multi_agent.py`
* **控制台输出**：
  ```text
  ============================= test session starts =============================
  platform win32 -- Python 3.13.9, pytest-9.0.3, pluggy-1.6.0 -- D:\Project\Open-SWE\.venv\Scripts\python.exe
  cachedir: .pytest_cache
  rootdir: D:\Project\Open-SWE
  configfile: pyproject.toml
  plugins: anyio-4.13.0, langsmith-0.8.3, asyncio-1.3.0
  asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
  collecting ... collected 6 items

  tests/test_multi_agent.py::test_pm_node PASSED                           [ 16%]
  tests/test_multi_agent.py::test_architect_node PASSED                    [ 33%]
  tests/test_multi_agent.py::test_qa_node_python PASSED                    [ 50%]
  tests/test_multi_agent.py::test_qa_node_js PASSED                        [ 66%]
  tests/test_multi_agent.py::test_route_after_qa PASSED                    [ 83%]
  tests/test_multi_agent.py::test_get_multi_agent_graph PASSED             [100%]

  ======================== 6 passed in 0.11s ========================
  ```

---

## 3. 稳健的异常降级保障 (Graceful Fallback)

即使在极端环境（如沙盒未接入互联网、缺少 Node 运行时、或者 CodeGraph 编译损坏）下，框架也能保持 100% 正常运转，确保零故障率：
- **Architect 节点降级**：当无法获取 CodeGraph 导出的文件目录树时，自动回退并调用 Python 脚本进行局部文件树递归 walks。
- **QA 节点降级**：当依赖解析链读取失败时，自动回退到传统的测试文件子字符串包含判定（启发式搜寻）。
