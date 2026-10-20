"""Build using an already-approved, project-local PyInstaller environment."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
import uuid

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', type=Path, default=ROOT/'.tools/windows-build/Scripts/python.exe')
    args = parser.parse_args()
    interpreter = args.python.resolve()
    if not interpreter.is_file():
        raise SystemExit('Approved local build environment is missing. See docs/WINDOWS_BETA.md; this script installs nothing.')
    cache = ROOT/'.cache/windows'; cache.mkdir(parents=True,exist_ok=True)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(cache/'pyinstaller-cache')
    local_tcl = ROOT/'.tools/windows-tcl'
    if local_tcl.is_dir():
        os.environ['TCL_LIBRARY'] = str(local_tcl/'tcl8.6')
        os.environ['TK_LIBRARY'] = str(local_tcl/'tk8.6')
    subprocess.run([str(interpreter),'-c','import tkinter; tkinter.Tcl()'],check=True)
    # Code-native icon, derived from the existing tray lamp; no new asset dependency.
    size=32; pixels=bytearray(); mask=bytearray(4*size)
    for y in range(size):
        for x in range(size):
            inside=(x-15.5)**2+(y-15.5)**2<=13**2
            pixels.extend((156,223,98,255) if inside else (0,0,0,0))
            if not inside:mask[y*4+x//8]|=1<<(7-x%8)
    import struct
    bitmap=struct.pack('<IIIHHIIIIII',40,size,size*2,1,32,0,len(pixels),0,0,0,0)+pixels+mask
    (cache/'AgentBeacon.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',size,size,0,0,1,32,len(bitmap),22)+bitmap)
    runtime_dir = cache/('runtime-'+uuid.uuid4().hex[:10])
    command=[str(interpreter),'-m','PyInstaller','--noconfirm','--clean','--distpath',str(runtime_dir),'--workpath',str(cache/'work')]
    subprocess.run([*command,str(ROOT/'scripts/windows/AgentBeacon.spec')],cwd=ROOT,check=True)
    runtime=runtime_dir/'AgentBeacon'
    # Keep the beginner guide available offline after installation as plain text.
    import shutil
    shutil.copy2(ROOT/'docs/INSTALL_UK.md', runtime/'USER-GUIDE-UK.txt')
    shutil.copy2(ROOT/'docs/INSTALL_EN.md', runtime/'USER-GUIDE-EN.txt')
    (cache/'latest-runtime.txt').write_text(str(runtime),encoding='utf-8')
    payload=cache/'payload.zip'
    with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(runtime.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(runtime).as_posix())
    digest=hashlib.sha256(payload.read_bytes()).hexdigest()
    (cache/'payload.sha256').write_text(digest,encoding='ascii')
    subprocess.run([str(interpreter),'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
                    '--name','AgentBeacon-Setup','--distpath',str(ROOT/'dist/windows'),
                    '--workpath',str(cache/'setup-work'),'--specpath',str(cache/'setup-spec'),
                    '--paths',str(ROOT),'--icon',str(cache/'AgentBeacon.ico'),
                    '--version-file',str(ROOT/'scripts/windows/version_info.txt'),
                    '--add-data',str(payload)+':.', '--add-data',str(cache/'payload.sha256')+':.',
                    str(ROOT/'scripts/windows/installer_entry.py')],cwd=ROOT,check=True)
    out=ROOT/'dist/windows'
    portable=out/'AgentBeacon-0.2.0-beta.4-windows-x64.zip'
    shutil.copy2(payload,portable)
    shutil.copy2(ROOT/'docs/INSTALL_UK.md',out/'USER-GUIDE-UK.txt')
    shutil.copy2(ROOT/'docs/INSTALL_EN.md',out/'USER-GUIDE-EN.txt')
    names=['AgentBeacon-Setup.exe',portable.name]
    (out/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256((out/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in names))
    (out/'BUILD-INFO.json').write_text(json.dumps({'version':'0.2.0-beta.4','android_version':'0.1.15','arch':'x64','python':sys.version.split()[0],
          'payload_sha256':digest,'status':'unsigned; code-level review passed; physical-phone and clean-machine acceptance required'},indent=2))
    print('Windows beta artifacts built in',out)


if __name__=='__main__':
    main()
