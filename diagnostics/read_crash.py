#!/usr/bin/python3
"""Print only DrClient's saved crash backtrace; do not change the system."""

import os
from pathlib import Path
import subprocess
import tempfile

import apport


def main():
    if os.geteuid() != 0:
        raise SystemExit("需要管理员身份读取 /var/crash 中的认证服务崩溃报告。")
    source = Path('/var/crash/_usr_bin_DrClient_drcomauthsvr.0.crash')
    report = apport.Report()
    with source.open('rb') as stream:
        report.load(stream)
    print('Executable:', report.get('ExecutablePath'), flush=True)
    print('Signal:', report.get('Signal'), flush=True)
    print('Date:', report.get('Date'), flush=True)
    if 'CoreDump' not in report:
        print(report.get('Stacktrace', '报告中没有 CoreDump 或 Stacktrace。'))
        return
    with tempfile.TemporaryDirectory(prefix='drclient-core-') as directory:
        core = Path(directory) / 'core'
        core.write_bytes(report['CoreDump'])
        subprocess.run([
            '/usr/bin/gdb', '-q', '-batch',
            '-ex', 'set auto-load off',
            '-ex', 'set debuginfod enabled off',
            '-ex', 'info registers eip eax ebx ecx edx',
            '-ex', 'x/8i $eip-8',
            '-ex', 'thread apply all bt 12',
            '/usr/bin/DrClient/drcomauthsvr', str(core),
        ], check=True)


if __name__ == '__main__':
    main()
