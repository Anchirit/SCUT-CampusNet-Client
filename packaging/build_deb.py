#!/usr/bin/python3
"""Build the amd64 desktop package without root or host installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = Path('usr/lib/mydrclient')
MAINTAINER = 'Anchirit <148778982+Anchirit@users.noreply.github.com>'


def run(*args):
    subprocess.run(args, check=True)


def main():
    if subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip() != 'amd64':
        raise SystemExit('本构建入口只支持 Ubuntu amd64。')
    for tool in ('gcc', 'dpkg-deb', 'desktop-file-validate'):
        if not shutil.which(tool):
            raise SystemExit('缺少构建工具：' + tool)
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise SystemExit('VERSION 必须为 major.minor.patch。')
    expected = json.loads((ROOT / 'vendor/SHA256SUMS.json').read_text())
    if set(expected) != {'drcomauthsvr', 'drcomauthsvr.drsc', 'drcomrulesvr.drsc', 'hostinfo.sh'}:
        raise SystemExit('认证后台清单不正确。')
    if expected['drcomauthsvr'] != 'ae8bd0b11a3606c8e85f47bf5e914a09e515c75cf83e333e8b722ee6952ef40c':
        raise SystemExit('认证后台不是已验证版本。')
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / 'vendor' / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('校验失败：' + name)
    # An explicit fixed epoch makes identical source builds reproducible.
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1793232000'))
    os.environ['SOURCE_DATE_EPOCH'] = str(epoch)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    package = output / f'mydrclient_{version}_amd64.deb'
    with tempfile.TemporaryDirectory(prefix='package-', dir=output) as temporary:
        stage = Path(temporary)

        def copy(source, target, mode=0o644):
            destination = stage / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, destination)
            destination.chmod(mode)

        # Deliberate allowlist: never package the checkout or user-generated files.
        for name in ('app.py', 'client.py', 'protocol.py', 'storage.py', 'tray.py',
                     'style.css', 'mydrclient.svg', 'VERSION'):
            copy(name, APP / name)
        for icon in sorted((ROOT / 'icons').rglob('*')):
            if icon.is_file():
                copy(icon.relative_to(ROOT), APP / icon.relative_to(ROOT))
        for name in expected:
            mode = 0o4755 if name == 'drcomauthsvr' else 0o755 if name == 'hostinfo.sh' else 0o644
            copy('vendor/' + name, APP / 'engine' / name, mode)
        copy('vendor/SHA256SUMS.json', APP / 'engine/SHA256SUMS.json')
        copy('packaging/io.github.Anchirit.MyDrClient.desktop',
             'usr/share/applications/io.github.Anchirit.MyDrClient.desktop')
        copy('mydrclient.svg', 'usr/share/icons/hicolor/scalable/apps/mydrclient.svg')
        copy('README.md', 'usr/share/doc/mydrclient/README.md')
        copy('PLAN.md', 'usr/share/doc/mydrclient/PLAN.md')
        copy('docs/verification.md', 'usr/share/doc/mydrclient/docs/verification.md')
        for name in ('preview.png', 'tray-icon.png', 'tray-menu.png'):
            copy('docs/images/' + name, 'usr/share/doc/mydrclient/docs/images/' + name)
        (stage / 'usr/share/doc/mydrclient/copyright').write_text(
            'Upstream: https://github.com/Anchirit/SCUT-CampusNet-Client\n'
            'Contact: ' + MAINTAINER + '\n\n'
            'Original Dr.COM binaries and resources remain the property of their\n'
            'respective rights holders. This is an independent frontend and\n'
            'compatibility integration, not an official vendor release.\n')
        binary = stage / 'usr/bin/mydrclient'
        binary.parent.mkdir(parents=True)
        run('gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-s',
            '-ffile-prefix-map=' + str(ROOT) + '=.',
            '-DAPP_INSTALL_DIR="/usr/lib/mydrclient"', '-DAPP_VERSION="' + version + '"',
            str(ROOT / 'launcher.c'), '-o', str(binary))
        binary.chmod(0o755)
        control = stage / 'DEBIAN'
        control.mkdir()
        size = sum(path.stat().st_size for path in (stage / 'usr').rglob('*') if path.is_file())
        (control / 'control').write_text(
            f'Package: mydrclient\nVersion: {version}\nArchitecture: amd64\n'
            'Section: net\nPriority: optional\nMaintainer: ' + MAINTAINER + '\n'
            f'Installed-Size: {(size + 1023) // 1024}\n'
            'Depends: libc6 (>= 2.34), python3 (>= 3.10), python3-gi, '
            'gir1.2-gtk-4.0 (>= 4.6), gir1.2-secret-1, gir1.2-gdkpixbuf-2.0, librsvg2-common, iproute2\n'
            'Homepage: https://github.com/Anchirit/SCUT-CampusNet-Client\n'
            'Description: Lightweight GTK 4 campus network authentication client\n'
            ' Chinese desktop frontend with tray support and a bundled Dr.COM engine.\n'
            ' The legacy privileged engine is installed root-owned with SetUID.\n')
        for name in ('preinst', 'prerm'):
            copy('packaging/engine-guard.sh', Path('DEBIAN') / name, 0o755)
        (control / 'md5sums').write_text(''.join(
            hashlib.md5(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(stage)) + '\n'
            for path in sorted((stage / 'usr').rglob('*')) if path.is_file()))
        run('desktop-file-validate', str(stage / 'usr/share/applications/io.github.Anchirit.MyDrClient.desktop'))
        for directory in (stage, *[p for p in stage.rglob('*') if p.is_dir()]):
            directory.chmod(0o755)
        for path in stage.rglob('*'):
            if path.is_file():
                path.chmod(path.stat().st_mode & 0o7755)
        for path in (stage, *stage.rglob('*')):
            os.utime(path, (epoch, epoch))
        run('dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(stage), str(package))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(digest + '  ' + package.name + '\n')
    print(package)
    print('SHA256: ' + digest)


if __name__ == '__main__':
    main()
