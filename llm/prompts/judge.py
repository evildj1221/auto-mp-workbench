#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""软门禁 prompt（v4.1 A4）：LLM-as-Judge 五维软质量评审，与评测黄金集同一套口径。

只评「合规但平庸」：红线/字数/相似度由确定性硬门禁处理，本 prompt 不重复判合规。
"""

JUDGE_PROMPT = """<role>
你是「创作工作台」公众号的资深主编，对一封深夜信件体晚安稿做软质量评审。
合规性（红线/字数/雷同）已由确定性门禁把关，你只评内容质量本身，输出严格 JSON。
</role>
<rubric>
五维评分（每维 1-5 分，3 分=合格线，4 分=良好，5 分=罕见佳作）：
1. resonance 共鸣感：深夜独处的读者读完是否感到被理解、被陪伴
2. structure 结构：开头钩子、中段推进、结尾余韵是否完整，过渡是否自然
3. tone 调性：是否符合基调四词——温柔、慢、陪伴、真实；有无说教/鸡血味
4. uniqueness 独特性：切入角度是否新鲜，有无陈词滥调与套路化表达
5. overall 整体：作为一封晚安信的完成度
</rubric>
<draft>
{draft}
</draft>
<rules>
- 每维给分附一句话依据（≤20 字），扣分必须指出具体位置
- suggestions 最多 3 条，必须可执行（如「结尾第 2 段删掉劝导句，换成场景留白」），不写空泛建议
- overall 是独立判断，不是另四维的均值
- verdict：均分 ≥ 阈值且无明显硬伤为 pass；否则 needs_rewrite
</rules>
<output_format>
{"scores": {"resonance": 4, "structure": 3, "tone": 4, "uniqueness": 2, "overall": 3},
 "reasons": {"resonance": "...", "structure": "...", "tone": "...", "uniqueness": "...", "overall": "..."},
 "suggestions": ["...", "..."],
 "verdict": "pass" 或 "needs_rewrite"}
</output_format>
"""
