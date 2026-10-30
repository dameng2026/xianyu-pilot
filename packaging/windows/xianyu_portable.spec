# -*- mode: python ; coding: utf-8 -*-
# Directory distribution only. Never switch to onefile / runtime extraction.
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata
root=Path(SPECPATH).resolve().parents[1]
web=root/'apps/web/dist'
if not (web/'desktop-safety.html').is_file():
    raise SystemExit('Build the original Vue project before packaging')
project_notices=[(str(root/'LICENSE'),'licenses/project')]
if (root/'NOTICE').is_file():
    project_notices.append((str(root/'NOTICE'),'licenses/project'))
a=Analysis(
    [str(root/'apps/api/run_portable.py')],pathex=[str(root/'apps/api')],
    binaries=[],datas=[(str(web),'web'),(str(root/'apps/api/app/desktop_migrations'),'app/desktop_migrations'),
        *project_notices]+copy_metadata('PySide6',recursive=True),
    hiddenimports=['app.portable.qt_host','app.portable.webengine_check','sqlalchemy.dialects.sqlite.aiosqlite',
        'aiosqlite','uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on','uvicorn.loops.asyncio'],
    hookspath=[],runtime_hooks=[str(root/'packaging/windows/portable_runtime_hook.py')],
    excludes=['PyQt5','PyQt6','PySide2','tkinter','pytest','IPython','matplotlib','numpy','pandas'],
    noarchive=False,
)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='XianyuPilot',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=True,contents_directory='_internal')
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='XianyuPilot')
