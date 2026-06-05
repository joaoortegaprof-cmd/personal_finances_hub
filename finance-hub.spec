# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

datas = [
    ('frontend/styles', 'frontend/styles'),
    ('frontend/assets', 'frontend/assets'),
]

hidden_imports = [
    # Models
    'backend.models.account',
    'backend.models.transaction',
    'backend.models.asset',
    'backend.models.debt',
    'backend.models.dividend',
    'backend.models.recurring',
    'backend.models.alert_history',
    # API
    'backend.api.app',
    'backend.api.routes.accounts',
    'backend.api.routes.transactions',
    'backend.api.routes.assets',
    'backend.api.routes.cards',
    'backend.api.routes.market',
    'backend.api.routes.dashboard',
    'backend.api.routes.debts',
    'backend.api.routes.simulation',
    'backend.api.routes.cashflow',
    'backend.api.routes.tax',
    'backend.api.routes.dividends',
    'backend.api.routes.import_routes',
    'backend.api.routes.recurring',
    'backend.api.routes.credit_score',
    # Services
    'backend.services.financial_summary_service',
    'backend.services.liquidity_service',
    'backend.services.alert_service',
    'backend.services.market_data_service',
    'backend.services.tax_service',
    'backend.services.simulation_service',
    'backend.services.cashflow_service',
    'backend.services.report_service',
    'backend.services.notification_service',
    'backend.services.notification_scheduler',
    'backend.services.import_service',
    'backend.services.risk_service',
    'backend.services.credit_score_service',
    'backend.services.expense_pattern_service',
    # SQLAlchemy
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.aiosqlite',
    'sqlalchemy.ext.asyncio',
    # Uvicorn internals
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    # FastAPI / Pydantic
    'fastapi',
    'fastapi.middleware.cors',
    'pydantic',
    'pydantic_settings',
    # Async
    'aiosqlite',
    'anyio',
    'anyio.from_thread',
    # Data
    'yfinance',
    'pandas',
    'numpy',
    # Plot
    'matplotlib',
    'matplotlib.backends.backend_qtagg',
    'matplotlib.backends.backend_agg',
    # Misc
    'loguru',
    'httpx',
    'httpx._transports.default',
    'loguru._logger',
    'plyer',
    'plyer.platforms.linux.notification',
    'dateutil',
    'dateutil.relativedelta',
    # Frontend components
    'frontend.components.api_client',
    'frontend.components.icons',
    'frontend.components.signals',
    'frontend.components.colors',
    'frontend.components.sector_mapper',
    'frontend.components.ticker_logo',
    # Frontend windows
    'frontend.windows.main_window',
    'frontend.windows.dashboard',
    'frontend.windows.transactions',
    'frontend.windows.accounts',
    'frontend.windows.cards',
    'frontend.windows.investments',
    'frontend.windows.market',
    'frontend.windows.reports',
    'frontend.windows.settings_page',
    'frontend.windows.goals_page',
    'frontend.windows.tax',
    'frontend.windows.simulation',
    'frontend.windows.cashflow',
    'frontend.windows.import_wizard',
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'PyQt5',
        'PySide2',
        'PySide6',
        'wx',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
    ],
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
    exclude_binaries=True,
    name='FinanceHub',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FinanceHub',
)
