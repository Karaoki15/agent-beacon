# Build both launchers from one analysis so their collected runtime is shared.
from pathlib import Path
import sys
root = Path(SPECPATH).resolve().parents[1]
pyinstaller_license = next((Path(sys.prefix)/'Lib/site-packages').glob('pyinstaller-*.dist-info/licenses/COPYING.txt'))
tk_license = root/'.tools/windows-tcl/tk8.6/license.terms'
if not tk_license.is_file():
    tk_license = Path(sys.base_prefix)/'tcl/tk8.6/license.terms'
desktop_entry = root / 'scripts/windows/desktop_entry.py'
hooks_entry = root / 'scripts/windows/hooks_entry.py'
a = Analysis([str(desktop_entry), str(hooks_entry)], pathex=[str(root)],
             binaries=[], datas=[(str(root/'agent_beacon/web'), 'agent_beacon/web'),
                                  (str(root/'dist/AgentBeacon-debug.apk'), 'dist'),
                                  (str(root/'LICENSE'), '.'),
                                  (str(root/'apps/windows/THIRD_PARTY_NOTICES.txt'), 'licenses'),
                                  (str(Path(sys.base_prefix)/'LICENSE.txt'), 'licenses'),
                                  (str(pyinstaller_license), 'licenses'),
                                  (str(tk_license), 'licenses')],
             hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=['tkinter','PIL'], noarchive=False)
pyz = PYZ(a.pure)
desktop_scripts = [item for item in a.scripts if item[0] != 'hooks_entry']
hook_scripts = [item for item in a.scripts if item[0] != 'desktop_entry']
desktop = EXE(pyz, desktop_scripts, [], exclude_binaries=True, name='AgentBeacon', debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=False,
              icon=str(root/'.cache/windows/AgentBeacon.ico'), version=str(root/'scripts/windows/version_info.txt'))
hooks = EXE(pyz, hook_scripts, [], exclude_binaries=True, name='AgentBeacon-Hooks', debug=False,
            bootloader_ignore_signals=False, strip=False, upx=False, console=True, hide_console='hide-early',
            icon=str(root/'.cache/windows/AgentBeacon.ico'), version=str(root/'scripts/windows/version_info.txt'))
coll = COLLECT(desktop, hooks, a.binaries, a.datas, strip=False, upx=False, name='AgentBeacon')
