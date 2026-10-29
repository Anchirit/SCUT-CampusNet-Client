#!/bin/sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ "$(id -u)" -eq 0 ]; then
    exec /usr/bin/python3 "$DIR/install.py"
elif [ -t 0 ]; then
    sudo /usr/bin/python3 "$DIR/install.py"
else
    pkexec /usr/bin/python3 "$DIR/install.py"
fi
chmod 0755 "$DIR/myDrClient"
/usr/bin/python3 - "$DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
applications = Path.home() / '.local/share/applications'
applications.mkdir(parents=True, exist_ok=True)
executable = str(root / 'myDrClient').replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
(applications / 'mydrclient.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=MyDrClient 校园网\n'
    'Comment=轻量校园网认证客户端\nExec="' + executable + '"\n'
    'Icon=' + str(root / 'mydrclient.svg') + '\n'
    'Terminal=false\nCategories=Network;\nStartupNotify=true\nStartupWMClass=mydrclient\n')
PY
echo '首次配置完成。请先退出旧客户端，再双击 myDrClient。'
