#!/bin/sh
# One-time setup. Ubuntu normally uses sudo/polkit rather than a root password.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ "$(id -u)" -eq 0 ]; then
    exec /bin/sh "$DIR/change.sh" "$DIR"
elif [ -t 0 ] && command -v sudo >/dev/null 2>&1; then
    exec sudo /bin/sh "$DIR/change.sh" "$DIR"
elif command -v pkexec >/dev/null 2>&1; then
    exec pkexec /bin/sh "$DIR/change.sh" "$DIR"
else
    echo '请在终端执行：sudo sh privillege.sh' >&2
    exit 1
fi
