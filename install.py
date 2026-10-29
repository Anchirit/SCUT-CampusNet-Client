#!/usr/bin/python3
"""Install only the privileged legacy engine; the desktop UI stays unprivileged."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
TARGET = Path('/usr/local/lib/mydrclient')


def main():
    if os.geteuid() != 0:
        raise SystemExit('请运行 privillege.sh 完成首次配置。')
    if platform.machine() != 'x86_64':
        raise SystemExit('随包认证后台仅支持 amd64 / x86_64。')
    check = subprocess.run(['/usr/bin/python3', '-c',
        "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Secret','1'); from gi.repository import Gtk, Secret"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode or not Path('/usr/sbin/ip').exists():
        subprocess.run(['apt-get', 'update'], check=True)
        subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends',
                        'python3-gi', 'gir1.2-gtk-4.0', 'gir1.2-secret-1', 'iproute2'], check=True)
    if os.statvfs('/usr/local/lib').f_flag & os.ST_NOSUID:
        raise SystemExit('/usr/local/lib 所在文件系统不支持 SetUID，未安装后台。')
    if TARGET.is_symlink():
        raise SystemExit('安装目录不能是符号链接。')
    for item in Path('/proc').iterdir():
        if item.name.isdecimal():
            try:
                if (item / 'exe').resolve() == TARGET / 'drcomauthsvr':
                    raise SystemExit('新版认证后台仍在运行，请退出 MyDrClient 后再更新。')
            except (OSError, PermissionError):
                pass
    expected = json.loads((ROOT / 'vendor/SHA256SUMS.json').read_text())
    required = {'drcomauthsvr', 'drcomauthsvr.drsc', 'drcomrulesvr.drsc', 'hostinfo.sh'}
    if set(expected) != required:
        raise SystemExit('发布包文件清单不正确，未安装。')
    if expected['drcomauthsvr'] != 'ae8bd0b11a3606c8e85f47bf5e914a09e515c75cf83e333e8b722ee6952ef40c':
        raise SystemExit('认证后台不是已验证的修复版本，未安装。')
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / 'vendor' / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('文件校验失败：' + name)
    with tempfile.TemporaryDirectory(prefix='.mydrclient-', dir='/usr/local/lib') as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        for name in required:
            destination = stage / name
            shutil.copyfile(ROOT / 'vendor' / name, destination)
            destination.chmod(0o4755 if name == 'drcomauthsvr' else 0o755 if name == 'hostinfo.sh' else 0o644)
        subprocess.run([str(stage / 'drcomauthsvr'), '-V'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=5)
        if TARGET.exists():
            backup = Path(tempfile.mkdtemp(prefix='mydrclient-', dir='/var/backups'))
            shutil.copytree(TARGET, backup / 'engine')
            print('原后台备份：' + str(backup), flush=True)
        TARGET.mkdir(mode=0o755, exist_ok=True)
        TARGET.chmod(0o755)
        os.chown(TARGET, 0, 0)
        for name in required:
            os.replace(stage / name, TARGET / name)
    print('后台已安装到 ' + str(TARGET) + '，图形界面无需管理员权限。', flush=True)


if __name__ == '__main__':
    main()
