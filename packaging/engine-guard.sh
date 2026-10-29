#!/bin/sh
set -eu

# Do not replace or remove an engine that is maintaining a live connection.
# Source installations use a different path and are deliberately left alone.
case "$1" in
    install|upgrade|remove|deconfigure)
        for entry in /proc/[0-9]*/exe; do
            executable=$(readlink "$entry" 2>/dev/null || true)
            case "$executable" in
                /usr/lib/mydrclient/engine/drcomauthsvr|'/usr/lib/mydrclient/engine/drcomauthsvr (deleted)')
                    echo 'MyDrClient: 请先从托盘菜单选择“退出程序”，再安装、升级或卸载。' >&2
                    exit 1
                    ;;
            esac
        done
        ;;
esac
