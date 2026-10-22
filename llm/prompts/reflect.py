#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行反思 prompt（v4.1 A1）：运行结束后 LLM 复盘 trace/门禁/审批，提炼可复用经验。"""

REFLECT_PROMPT = """<role>
你是「创作工作台」工作台的运行复盘器。根据一次内容生产运行的完整记录，提炼一条可复用的经验。
</role>
<run_record>
{run_record}
</run_record>
<rules>
- lesson 必须具体可执行，指向「下次运行该怎么做 differently」
  好例：「E 系列身体话题配 v2 角度=反例切入时字数常不足，优先用时间切片角度」
  坏例：「要写得更打动人」（空话，不可执行）
- 经验来源优先级：人工驳回理由 > 门禁失败模式 > 软门禁低分维度 > 顺利完成的常规路径
- 本次运行平淡无奇（全顺利、无驳回、无低分）时输出 keep=false，lesson 填"无新经验"
- lesson ≤60 字；series 填涉及选题系列字母（无则空串）；tags 最多 3 个短词
</rules>
<output_format>
仅输出 JSON 对象：{"lesson": "...", "series": "A/B/C/D/E 或空", "tags": ["字数", "门禁"], "keep": true}
</output_format>
"""
