"""persona-distiller：把一个人的知识库蒸馏成可复用的人格 Skill。

模块划分：
    util       通用工具（路径、JSON、原子写、HTML 转文本）
    config     工作区与 LLM 配置
    store      SQLite 知识库（sources / chunks / personas）
    extract    文档、书籍、链接的文本抽取
    chunk      切片与语录挖掘
    style      语言风格与思维模式分析
    retrieve   BM25 检索（中英文）
    llm        OpenAI 兼容的 LLM 客户端
    distill    生成 SKILL.md / persona.json / 提示词模板
    agent      人格运行时（ask / chat）
    cli        命令行入口
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
