#!/usr/bin/env python
"""
Office Agent 数据库管理工具

用法:
    python manage.py init_db          # 初始化数据库（创建表）
    python manage.py drop_db          # 删除所有表
    python manage.py reset_db         # 重置数据库（删除后重建）
    python manage.py seed             # 填充初始数据
    python manage.py migrate          # 运行 Alembic 迁移
    python manage.py makemigrations   # 生成迁移（需 alembic）
    python manage.py status           # 查看数据库状态
"""
import sys
import os

# 确保项目根目录在 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def init_db():
    """初始化数据库"""
    from office_agent.database.connection import init_db, DATABASE_URL
    print(f"正在初始化数据库: {DATABASE_URL}")
    init_db(drop_all=False)
    print("✅ 数据库表创建完成")


def drop_db():
    """删除所有表"""
    from office_agent.database.connection import init_db, DATABASE_URL
    print(f"正在删除数据库表: {DATABASE_URL}")
    confirm = input("确认删除所有表？(yes/no): ")
    if confirm.lower() != "yes":
        print("已取消")
        return
    init_db(drop_all=True)
    print("✅ 所有表已删除")


def reset_db():
    """重置数据库"""
    from office_agent.database.connection import init_db, DATABASE_URL
    print(f"正在重置数据库: {DATABASE_URL}")
    confirm = input("确认删除所有数据并重建数据库？(yes/no): ")
    if confirm.lower() != "yes":
        print("已取消")
        return
    init_db(drop_all=True)
    init_db(drop_all=False)
    print("✅ 数据库已重置")
    seed()


def seed():
    """填充初始数据"""
    from office_agent.database.session import session_scope
    from office_agent.database.repository import (
        AgentRepository, SkillRepository,
    )

    print("正在填充初始数据...")

    with session_scope() as session:
        agent_repo = AgentRepository(session)
        skill_repo = SkillRepository(session)

        # 内置 Agent
        builtin_agents = [
            ("word_agent", "Word Agent", "word",
             "Word文档智能处理Agent，支持自动排版、格式转换、模板套用",
             '["排版","格式转换","模板套用","公文生成","论文排版","样式统一"]'),
            ("ppt_agent", "PPT Agent", "ppt",
             "PPT智能生成Agent，根据大纲自动生成演示文稿",
             '["生成PPT","设计模板","内容规划","幻灯片制作","数据可视化"]'),
            ("excel_agent", "Excel Agent", "excel",
             "Excel智能处理Agent，支持数据分析、图表生成、公式编写",
             '["数据分析","公式生成","图表制作","数据清洗","统计报表"]'),
            ("orchestrator", "Orchestrator", "orchestrator",
             "总控Agent，负责意图识别、任务分发和多Agent协作",
             '["意图识别","任务路由","多Agent协作","工作流编排"]'),
        ]
        for aid, name, atype, desc, caps in builtin_agents:
            if not agent_repo.get_by_agent_id(aid):
                agent_repo.create_agent(aid, name, atype, desc, capabilities=caps)
                print(f"  + Agent: {name}")

        # 内置 Skill
        builtin_skills = [
            ("论文排版", "word", "学术论文格式排版，支持学校格式规范", "word"),
            ("公文写作", "word", "政府/企业公文格式生成", "word"),
            ("商业PPT", "ppt", "商业汇报PPT生成，含设计模板", "ppt"),
            ("答辩PPT", "ppt", "毕业/项目答辩PPT生成", "ppt"),
            ("销售数据分析", "excel", "销售数据统计与图表分析", "excel"),
            ("成绩统计", "excel", "学生成绩统计分析", "excel"),
        ]
        for name, cat, desc, stype in builtin_skills:
            if not skill_repo.find_one(name=name):
                skill_repo.create_skill(name=name, skill_type=stype,
                                        description=desc, category=cat)
                print(f"  + Skill: {name}")

    print("✅ 初始数据填充完成")


def migrate():
    """运行 Alembic 迁移"""
    import subprocess
    db_dir = os.path.join(os.path.dirname(__file__), "office_agent", "database")
    ini_path = os.path.join(db_dir, "alembic.ini")
    result = subprocess.run(
        ["alembic", "-c", ini_path, "upgrade", "head"],
        cwd=db_dir,
    )
    if result.returncode == 0:
        print("✅ 迁移完成")
    else:
        print("❌ 迁移失败")


def makemigrations(message: str = "auto migration"):
    """生成迁移文件"""
    import subprocess
    db_dir = os.path.join(os.path.dirname(__file__), "office_agent", "database")
    ini_path = os.path.join(db_dir, "alembic.ini")
    result = subprocess.run(
        ["alembic", "-c", ini_path, "revision", "--autogenerate", "-m", message],
        cwd=db_dir,
    )
    if result.returncode == 0:
        print("✅ 迁移文件已生成")
    else:
        print("❌ 生成失败")


def status():
    """查看数据库状态"""
    from office_agent.database.connection import DATABASE_URL, engine
    from office_agent.database.session import session_scope
    from office_agent.database.repository import (
        UserRepository, FileRepository, TaskRepository, AgentRepository,
        SkillRepository, TemplateRepository, KnowledgeRepository,
        ExecutionLogRepository,
    )

    print(f"数据库: {DATABASE_URL}")
    print(f"引擎: {engine.url}")
    print()

    with session_scope() as session:
        repos = {
            "用户": UserRepository(session),
            "文件": FileRepository(session),
            "任务": TaskRepository(session),
            "Agent": AgentRepository(session),
            "技能": SkillRepository(session),
            "模板": TemplateRepository(session),
            "知识": KnowledgeRepository(session),
            "日志": ExecutionLogRepository(session),
        }
        print(f"{'表':<10} {'记录数':>8}")
        print("-" * 22)
        for name, repo in repos.items():
            print(f"{name:<10} {repo.count():>8}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "init_db":
        init_db()
    elif cmd == "drop_db":
        drop_db()
    elif cmd == "reset_db":
        reset_db()
    elif cmd == "seed":
        seed()
    elif cmd == "migrate":
        migrate()
    elif cmd == "makemigrations":
        msg = sys.argv[2] if len(sys.argv) > 2 else "auto migration"
        makemigrations(msg)
    elif cmd == "status":
        status()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
