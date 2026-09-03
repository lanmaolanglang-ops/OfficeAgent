"""TaskType 三套定义合一的专项测试（清单 503）。

合一后的格局：
- models.schemas.TaskType        —— 产品类型（word/ppt/excel/...），独立保留；
- models.schemas.ExcelTaskType   —— Excel 任务子类型唯一定义（两套旧定义并集）；
- excel_agent.models.TaskType    —— 上述枚举的兼容别名（旧 import 路径不破）；
- models.model_schemas.AITaskType —— 模型选型能力分类，命名与职责均不同。
"""
from office_agent.excel_agent.models import ExcelTask
from office_agent.excel_agent.models import TaskType as AgentTaskType
from office_agent.models.schemas import ExcelTaskType, TaskType as ProductTaskType
from office_agent.models.model_schemas import AITaskType


class TestSingleDefinition:
    def test_excel_agent_tasktype_is_alias(self):
        assert AgentTaskType is ExcelTaskType

    def test_package_level_export_is_alias(self):
        from office_agent.excel_agent import TaskType as Exported
        assert Exported is ExcelTaskType

    def test_no_second_enum_class_defined(self):
        """excel_agent.models 不得再出现 class TaskType（源码守卫）。"""
        import inspect

        from office_agent.excel_agent import models
        assert "class TaskType" not in inspect.getsource(models)

    def test_excel_task_default_uses_canonical_enum(self):
        assert ExcelTask().task_type is ExcelTaskType.UNKNOWN


class TestUnionValues:
    def test_former_agent_only_values_available(self):
        # 原 excel_agent.TaskType 独有取值在合一后仍可解析
        for value in ("read", "format", "pivot", "filter", "merge",
                      "template", "unknown"):
            assert ExcelTaskType(value).value == value

    def test_former_schemas_only_values_available(self):
        # 原 schemas.ExcelTaskType 独有取值不丢失
        for value in ("formula", "clean"):
            assert ExcelTaskType(value).value == value

    def test_old_string_values_unchanged(self):
        # 序列化/数据库中已存取值必须原样可解析
        for value in ("calculate", "analyze", "chart", "create"):
            assert ExcelTaskType(value).name.isupper()


class TestLayerBoundaries:
    def test_product_tasktype_remains_separate(self):
        assert ProductTaskType.WORD.value == "word"
        assert ProductTaskType is not ExcelTaskType

    def test_ai_tasktype_remains_separate(self):
        assert AITaskType.PPT_CONTENT.value == "ppt_content"
        assert AITaskType is not ExcelTaskType
