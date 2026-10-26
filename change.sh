#!/bin/sh
# Run by privillege.sh with administrator privileges, once per installation.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo '需要管理员权限，请运行 privillege.sh。' >&2
    exit 1
fi
DIR=$(CDPATH= cd -- "${1:-$(dirname -- "$0")}" && pwd -P)
cd "$DIR"
if [ "$(uname -m)" != x86_64 ]; then
    echo '此客户端发布包只支持 x86_64 / amd64。' >&2
    exit 1
fi

# The original GUI bundles Qt 4; install only its missing system libraries.
missing=0
if ! command -v python3 >/dev/null 2>&1; then missing=1; fi
for program in DrClientLinux DrClientLinux.qt4; do
    if ldd "./$program" 2>&1 | /bin/grep -q 'not found'; then missing=1; fi
done
if [ "$missing" -ne 0 ]; then
    echo '安装 Ubuntu 图形运行库和 python3……'
    apt-get update
    apt-get install -y python3 libx11-6 libxext6 libxrender1 libfontconfig1 \
        libfreetype6 libstdc++6 libgcc-s1 zlib1g
fi

# Check the version before granting the existing helper its required privilege.
/usr/bin/python3 - <<'PY'
import hashlib
from pathlib import Path
expected = {
    'drcomauthsvr': 'ae8bd0b11a3606c8e85f47bf5e914a09e515c75cf83e333e8b722ee6952ef40c',
    'DrClientLinux.qt4': '31b302ab432da3887aa875e049f6277ff64bc3d1fce7cf1595cd93890a9b3b27',
}
for name, digest in expected.items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
        raise SystemExit('程序版本或完整性不符，停止配置：' + name)
PY
chown root:root ./drcomauthsvr
chmod 4755 ./drcomauthsvr
chmod 0755 ./DrClientLinux ./DrClientLinux.qt4 ./hostinfo.sh ./privillege.sh ./change.sh
echo '检查文件、网卡和认证后台通信……'
/usr/bin/python3 ./diagnostics/check.py --service-test
echo '配置完成。以后（包括重启后）直接双击此目录中的 DrClientLinux。'
