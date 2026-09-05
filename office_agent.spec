# -*- mode: python ; coding: utf-8 -*-
"""
OfficeAgent PyInstaller Spec
打包命令: pyinstaller office_agent.spec --clean
输出: dist/OfficeAgent/
"""
import sys
import os
from pathlib import Path

block_cipher = None

# 项目根目录
project_root = Path(SPECPATH)

# 隐藏导入
hiddenimports = [
    # Web框架
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'pydantic',
    'pydantic.deprecated.decorator',
    'email_validator',
    'multipart',
    # Office处理
    'docx',
    'pptx',
    'openpyxl',
    'pandas',
    'numpy',
    'usearch',
    'usearch.index',
    'usearch.compiled',
    'fitz',
    'PIL',
    # 数据库
    'sqlalchemy',
    'sqlalchemy.dialects.sqlite',
    'alembic',
    # 安全
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat.primitives.kdf.pbkdf2',
    # 系统
    'psutil',
    'win32service',
    'win32serviceutil',
    'win32event',
    'servicemanager',
    # 标准库
    'json',
    'urllib',
    'urllib.request',
    'urllib.error',
    'http',
    'http.server',
    'threading',
    'multiprocessing',
    'concurrent.futures',
    'logging.handlers',
    'pathlib',
    'dataclasses',
    'enum',
    'uuid',
    'hashlib',
    'hmac',
    'base64',
    'secrets',
    'gzip',
    'zipfile',
    'tarfile',
    'shutil',
    'tempfile',
    'signal',
    'socket',
    'subprocess',
    'argparse',
]

# 数据文件
datas = []
# 添加配置模板
config_dir = project_root / 'release' / 'config'
if config_dir.exists():
    datas.append((str(config_dir), 'config'))

# 排除不需要的模块
excludes = [
    'tkinter',
    'matplotlib',
    'scipy',
    'sklearn',
    'statsmodels',
    'celery',
    'redis',
    'boto3',
    'botocore',
    'minio',
    'pytest',
    'pytest_cov',
    'IPython',
    'jupyter',
    'notebook',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',
    'pygame',
    'cv2',
    'tensorflow',
    'torch',
    'transformers',
]

a = Analysis(
    [str(project_root / 'desktop' / 'app_launcher.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='OfficeAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI模式，不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / 'desktop' / 'resources' / 'app.ico') if (project_root / 'desktop' / 'resources' / 'app.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OfficeAgent',
)
