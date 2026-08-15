# Testing & Quality Assurance

Office Agent 测试与质量保证系统。

## 测试结构

```
tests/
├── __init__.py
├── conftest.py              # 共享fixtures
├── pytest.ini               # pytest配置
├── unit/                    # 单元测试
│   ├── test_word.py         # Word引擎测试
│   ├── test_ppt.py          # PPT引擎测试
│   ├── test_excel.py        # Excel引擎测试
│   ├── test_database.py     # 数据库测试
│   ├── test_storage.py      # 存储测试
│   ├── test_security.py     # 安全测试
│   └── test_config.py       # 配置/日志测试
├── integration/             # 集成测试
│   ├── test_workflow.py     # 多Agent工作流测试
│   ├── test_api.py          # API接口测试
│   └── test_e2e.py          # 端到端文件处理测试
├── agent_eval/              # Agent评估模块
│   ├── evaluator.py         # 评估器基类
│   ├── word_eval.py         # Word Agent评估
│   ├── ppt_eval.py          # PPT Agent评估
│   └── excel_eval.py        # Excel Agent评估
├── performance/             # 性能测试
│   ├── test_performance.py  # 性能基准测试
│   └── test_stress.py       # 压力测试
├── test_report.py           # 测试报告生成
├── fixtures/                # 测试数据目录
└── reports/                 # 测试报告输出
```

## 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行单元测试
python -m pytest tests/unit/ -v

# 运行集成测试
python -m pytest tests/integration/ -v

# 运行性能测试
python -m pytest tests/performance/ -v

# 运行特定模块
python -m pytest tests/unit/test_word.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=office_agent --cov-report=html

# 生成测试报告
python -m pytest tests/ --json-report --json-report-file=tests/reports/report.json
```

## 测试覆盖

### 单元测试 (Unit Tests)

| 模块 | 测试内容 |
|------|---------|
| Word Engine | 文档解析、格式引擎、质量检查、结构分析 |
| PPT Engine | 演示文稿解析、内容规划、质量检查、模板分析 |
| Excel Engine | 电子表格解析、数据分析、公式引擎、质量检查、数据清洗 |
| Database | 会话管理、数据模型、Repository |
| Storage | 文件存储、存储配置 |
| Security | 密码哈希、JWT、RBAC、访问控制、文件安全、Prompt安全、沙箱 |
| Config | 配置系统、日志系统 |

### 集成测试 (Integration Tests)

| 模块 | 测试内容 |
|------|---------|
| Workflow | DAG图、审批节点、任务分解、Agent选择、故障恢复、模板 |
| API | 应用创建、路由注册 |
| E2E | Word/Excel/PPT完整流程、认证流程、文件上传安全、沙箱数据分析 |

### 性能测试 (Performance Tests)

| 测试 | 内容 |
|------|------|
| Word解析性能 | 100次迭代基准测试 |
| Excel解析性能 | 100次迭代基准测试 |
| JWT性能 | 1000次创建/验证 |
| 密码哈希性能 | 100次哈希 |
| Prompt扫描性能 | 100次扫描 |
| 沙箱执行性能 | 简单代码执行 |
| 并发Word解析 | 10并发线程 |
| 并发Excel解析 | 10并发线程 |
| 并发认证 | 20并发线程 |
| 并发文件扫描 | 50并发线程 |
| 并发沙箱执行 | 5并发进程 |
| 损坏文件处理 | 错误恢复测试 |
| 缺失文件处理 | 错误恢复测试 |
| 沙箱错误恢复 | 异常处理测试 |

## Agent评估

### Word Agent评估指标
- 格式准确率（字体、字号、行距、标题格式）
- 内容完整性
- 结构合理性
- 表格格式正确性

### PPT Agent评估指标
- 设计评分（布局、配色、字体一致性）
- 内容密度
- 幻灯片数量合理性
- 图表正确性

### Excel Agent评估指标
- 分析准确率
- 公式正确性
- 图表生成
- 预测合理性

使用示例：
```python
from tests.agent_eval import WordEvaluator

evaluator = WordEvaluator()
result = evaluator.evaluate_quality("document.docx")
print(f"Score: {result.total_score}/100")
print(f"Status: {result.status}")
```

## 测试报告

测试报告系统支持JSON和Markdown格式输出：

```python
from tests.test_report import TestRunner

runner = TestRunner()
runner.run_test("test_name", "module", "unit", test_function)
report = runner.finish()
report.print_summary()
report.to_json("report.json")
report.to_markdown("report.md")
```

## CI/CD

GitHub Actions配置在 `.github/workflows/ci.yml`：

- **触发条件**：push到main/develop分支，PR到main
- **Python版本**：3.10, 3.11, 3.12
- **步骤**：
  1. 代码检查（ruff）
  2. 单元测试
  3. 集成测试
  4. Agent评估
  5. 生成测试报告
  6. 上传覆盖率到Codecov

## 测试数据

测试使用conftest.py中的fixtures自动生成临时文件：
- `sample_docx`：包含标题、段落、表格的Word文档
- `sample_xlsx`：包含销售数据的Excel表格
- `sample_pptx`：包含标题和内容的PPT演示文稿
- `temp_dir`：临时目录（测试后自动清理）
- `security_config`：安全配置

## 编写新测试

1. 在对应目录创建测试文件
2. 使用pytest风格的测试函数
3. 使用conftest.py中的fixtures
4. 添加标记：`@pytest.mark.unit`、`@pytest.mark.integration`等
5. 确保测试独立、可重复、无外部依赖

## 版本历史

- **v0.46.0** (2026-07-31)：初始测试系统
  - 102个测试全部通过
  - 单元测试、集成测试、性能测试、压力测试
  - Agent评估模块
  - 测试报告系统
  - CI/CD配置
