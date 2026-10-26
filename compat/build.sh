#!/bin/sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$DIR"
/usr/bin/python3 compat/patch_backend.py
if [ ! -f DrClientLinux.qt4 ]; then
    cp -p originals/DrClientLinux DrClientLinux.qt4
fi
gcc -O2 -Wall -Wextra -Werror -o .DrClientLinux.new compat/launcher.c -lX11
mv .DrClientLinux.new DrClientLinux
echo '构建完成。首次使用或移动后请执行 sh privillege.sh。'
