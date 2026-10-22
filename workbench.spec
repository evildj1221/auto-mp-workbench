# PyInstaller 打包规格 · 创作工作台（桌面客户端版 · v5.1 前后端分离架构）
# 用法：pyinstaller workbench.spec --noconfirm --clean
# 前置：cd frontend && npm install && npm run build（产物 frontend/dist 由本 spec 打包）
# 打包形态：onedir（dist/创作工作台/{exe,_internal}，拷到项目根使用）——
# onefile 每次启动/每次按钮点击都要解包 289MB（实测 8-9s），onedir 免解包秒开。
# -*- mode: python ; coding: utf-8 -*-
import os
import certifi
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 关键：requests 依赖 certifi 的 cacert.pem 做 HTTPS 验证，PyInstaller 默认不打包 → 导致 exe 调用
# DeepSeek API/微信 API 时 OSError: Could not find a suitable TLS CA certificate bundle
cert_pem = certifi.where()

# v4.0 Agent 依赖：langgraph 内部惰性加载（prebuilt/checkpoint），静态分析找不到 → collect 全量
_agent_hidden = (
    collect_submodules('langgraph')
    + collect_submodules('langgraph.checkpoint')
    + collect_submodules('langchain_core')
    + collect_submodules('pydantic')
    + [
        # 六层拆包新模块（runner 是 try/except 导入，显式声明保险）
        'agent', 'agent.graph', 'agent.planner', 'agent.executor',
        'agent.interrupts', 'agent.state', 'agent.config', 'agent.gates',
        'tools', 'tools.registry', 'tools._base',
        'tools.topic_tools', 'tools.write_tools', 'tools.quality_tools',
        'tools.publish_tools', 'tools.memory_tools', 'tools.state_tools',
        'pipeline.runner',
        'core.errors', 'core.logging',
    ]
)

# v5.0 后端 API 全模块（FastAPI 路由显式声明保险）
_backend_hidden = [
    'backend', 'backend.app', 'backend.app.main', 'backend.app.common',
    'backend.app.taskrunner', 'backend.app.schemas',
    'backend.app.api', 'backend.app.api.agent', 'backend.app.api.topics',
    'backend.app.api.drafts', 'backend.app.api.export', 'backend.app.api.review',
    'backend.app.api.archive', 'backend.app.api.settings', 'backend.app.api.typography',
    'backend.app.api.dashboard', 'backend.app.api.tasks',
]

datas = [
    ('frontend/dist', 'frontend/dist'),          # v5.0：SPA 构建产物（替代原 templates）
    ('排版视觉规范.md', '.'),
    ('创作工作台视觉设计系统v3-完整版.md', '.'),     # v3：出图视觉规范（头图 prompt 依据）
    (cert_pem, 'certifi'),  # P0 修复：让 requests 在 exe 内能找到 CA 证书
]

a = Analysis(
    ['run_workbench.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'fastapi', 'uvicorn', 'multipart', 'markdown', 'dotenv', 'requests',
        'certifi', 'certifi.core',  # P0 修复：让 certifi.where() 在 exe 内指向正确路径
        'cat_shop', 'cli',
        # v4 六层拆包模块（Phase 1）
        'core', 'core.paths', 'core.storage', 'core.memory', 'core.redlines',
        'core.quality', 'core.scoring', 'core.render',
        'core.exporter', 'core.wechat', 'core.charts', 'core.directives',
        'core.typography', 'core.typography.manager', 'core.typography.theme',
        'core.typography.registry', 'core.typography.safety', 'core.calendar_ctx',
        'core.ccp', 'core.inline_images', 'core.pipeline_status',
        'llm', 'llm.provider', 'llm.client', 'llm.prompts',
        'llm.prompts.topic_generator', 'llm.prompts.score_rubric',
        'llm.prompts.writer', 'llm.prompts.title_generator',
        'llm.prompts.feedback_analyzer', 'llm.prompts.semantic_redline',
        'llm.image_provider',
        'pipeline', 'pipeline.topics', 'pipeline.writing',
        'pipeline.publish', 'pipeline.flow',
        *_backend_hidden,
        # v4.0 Agent 全家桶（Phase 6）
        *_agent_hidden,
        # pywebview 及其 Windows 后端
        'webview', 'webview.platforms.edgechromium', 'webview.platforms.winforms',
        'clr_loader', 'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'PIL', 'tkinter', 'PyQt5', 'PySide2',
              'flask', 'werkzeug'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir：二进制/数据放 _internal，启动免解包
    name='创作工作台',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,                # 无控制台黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/cat.ico',        # 创作工作台图标（scripts/make_icon.py 生成）
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='创作工作台',
)
