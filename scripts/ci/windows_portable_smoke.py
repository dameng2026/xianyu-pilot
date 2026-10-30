"""Probe EXE from unrelated cwd, then move the entire isolated test copy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(bundle,workspace,*,webengine=True):
    workspace=workspace.resolve()
    workspace.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portable-smoke-',dir=workspace) as temporary:
        root=Path(temporary)
        first=root/'中文 portable A'/'XianyuPilot'
        second=root/'移动盘符模拟 B'/'XianyuPilot'
        outside=root/'unrelated-cwd';outside.mkdir()
        forbidden=root/'inherited-user-and-temp';forbidden.mkdir()
        env=dict(os.environ)
        env.update({'PATH':str(Path(os.environ['SystemRoot'])/'System32'),'APPDATA':str(forbidden),
                    'LOCALAPPDATA':str(forbidden),'TEMP':str(forbidden),'TMP':str(forbidden)})
        for key in ('PYTHONHOME','PYTHONPATH','QTWEBENGINE_CHROMIUM_FLAGS','QTWEBENGINE_REMOTE_DEBUGGING'):
            env.pop(key,None)
        shutil.copytree(bundle,first)
        def probe(folder,flag):
            result=subprocess.run([str(folder/'XianyuPilot.exe'),flag,'--port','19177'],cwd=outside,env=env,timeout=90,check=False)
            if result.returncode:
                raise RuntimeError(f'Frozen {flag} failed: {result.returncode}')
        probe(first,'--self-check')
        before=json.loads((first/'data/logs/portable-self-check.json').read_text(encoding='utf-8'))
        if not before['frozen'] or Path(before['dataRoot']).resolve()!=first/'data':
            raise ValueError('frozen data root escaped')
        second.parent.mkdir()
        # Both checked absolute paths are inside this disposable test workspace.
        if not first.resolve().is_relative_to(root) or not second.resolve().is_relative_to(root):
            raise ValueError('unsafe relocation target')
        shutil.move(str(first),str(second))
        probe(second,'--self-check')
        after=json.loads((second/'data/logs/portable-self-check.json').read_text(encoding='utf-8'))
        if after['storageToken']!=before['storageToken'] or Path(after['dataRoot']).resolve()!=second/'data':
            raise ValueError('portable relocation lost database identity')
        if webengine:
            probe(second,'--webengine-check')
            if not json.loads((second/'data/logs/webengine-check.json').read_text())['ok']:
                raise ValueError('embedded Chromium did not render Vue')
        if list(outside.iterdir()):
            raise ValueError('application wrote data relative to the calling cwd')
        if list(forbidden.iterdir()):
            raise ValueError('application used inherited user or Temp directories')
        return {'frozenStartup':True,'externalCwd':True,'relocatedDatabase':True,'embeddedVueRendered':webengine,
                'inheritedUserTempUntouched':True,'developerPathRemoved':True,
                'nativeTrayValidated':False,'cleanVmValidated':False,'crossVolumeValidated':False,'fullIoIsolationValidated':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,default=Path('.tmp/portable-validation'))
    parser.add_argument('--report',type=Path,default=Path('reports/windows-portable-smoke.json'))
    args=parser.parse_args()
    result=run(args.directory.resolve(),args.workspace)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
