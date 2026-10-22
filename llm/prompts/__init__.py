#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm/prompts/ · 全部 prompt 常量（自 cat_shop.py 内嵌段原样迁出）。

注入方式沿用 .replace() 占位符替换，不引入模板引擎。
"""
from llm.prompts.topic_generator import TOPIC_GENERATOR_PROMPT  # noqa: F401
from llm.prompts.score_rubric import SCORE_RUBRIC_PROMPT  # noqa: F401
from llm.prompts.writer import WRITER_V1_PROMPT, OUTLINE_PROMPT  # noqa: F401
from llm.prompts.title_generator import TITLE_GENERATOR_PROMPT  # noqa: F401
from llm.prompts.feedback_analyzer import FEEDBACK_ANALYZER_PROMPT  # noqa: F401
from llm.prompts.semantic_redline import SEMANTIC_REDLINE_PROMPT  # noqa: F401
from llm.prompts.reflect import REFLECT_PROMPT  # noqa: F401
from llm.prompts.judge import JUDGE_PROMPT  # noqa: F401
from llm.prompts.cover import (COVER_SCENE_PROMPT, build_cover_prompt)  # noqa: F401
