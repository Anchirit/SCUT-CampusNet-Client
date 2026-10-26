#!/usr/bin/python3
"""Check the package and optionally smoke-test the privileged backend."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import tempfile
import time

from protocol import decrypt, encrypt

ROOT = Path(__file__).resolve().parent.parent
BACKEND_HASH = 'ae8bd0b11a3606c8e85f47bf5e914a09e515c75cf83e333e8b722ee6952ef40c'
QT_HASH = '31b302ab432da3887aa875e049f6277ff64bc3d1fce7cf1595cd93890a9b3b27'


def package_check():
    failures = []
    def result(ok, description):
        print(('[通过] ' if ok else '[失败] ') + description, flush=True)
        if not ok:
            failures.append(description)
    result(platform.machine() == 'x86_64', 'CPU 为 x86_64（此发布包支持 Ubuntu 22.04 amd64）')
    flags = os.statvfs(ROOT).f_flag
    result(not flags & os.ST_NOSUID, '程序所在文件系统允许 SetUID')
    result(not flags & os.ST_NOEXEC, '程序所在文件系统允许执行程序')
    for name in ('DrClientLinux', 'DrClientLinux.qt4', 'drcomauthsvr',
                 'DrClientLinux.rcc', 'drcomauthsvr.drsc', 'drcomrulesvr.drsc',
                 'hostinfo.sh', 'translator/localizer_chs.qm'):
        result((ROOT / name).is_file(), '文件存在：' + name)
    for name, digest in (('drcomauthsvr', BACKEND_HASH), ('DrClientLinux.qt4', QT_HASH)):
        path = ROOT / name
        if path.is_file():
            result(hashlib.sha256(path.read_bytes()).hexdigest() == digest, '程序版本和完整性：' + name)
    service = ROOT / 'drcomauthsvr'
    if service.is_file():
        metadata = service.stat()
        result(metadata.st_uid == 0 and metadata.st_gid == 0 and bool(metadata.st_mode & stat.S_ISUID),
               '认证后台由 root:root 所有且具有 SetUID 权限')
    for name in ('DrClientLinux', 'DrClientLinux.qt4'):
        process = subprocess.run(['/usr/bin/ldd', str(ROOT / name)], capture_output=True, text=True)
        result(process.returncode == 0 and 'not found' not in process.stdout,
               '共享库完整：' + name)
    process = subprocess.run(['/bin/sh', str(ROOT / 'hostinfo.sh')], capture_output=True, text=True)
    result(process.returncode == 0 and any(line.startswith('Net\t') for line in process.stdout.splitlines()),
           '网卡脚本能够读取至少一个 IPv4 地址和网卡信息')
    print('提示：以上检查不代表校园网账号已认证成功。', flush=True)
    return failures


def service_test():
    if os.geteuid() != 0:
        raise RuntimeError('后台启动自检需要管理员权限，请运行 privillege.sh。')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        try:
            channel.bind(('127.0.0.1', 2081))
        except OSError as error:
            raise RuntimeError('请先退出已打开的 DrClient 客户端，再重新运行 privillege.sh。') from error
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind(('127.0.0.1', 2080))
            except OSError as error:
                raise RuntimeError('2080 端口已被其他认证后台占用，请先退出旧客户端。') from error
        channel.settimeout(0.4)
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([str(ROOT / 'drcomauthsvr'), '-f', str(ROOT), '-l', '1'],
                                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'LANG': 'zh_CN.UTF-8'})
            responses = {}
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and len(responses) < 3:
                    if process.poll() is not None:
                        raise RuntimeError('认证后台提前退出，退出码：%s' % process.returncode)
                    for query in ('AUT', 'ADP', 'VER'):
                        if query not in responses:
                            channel.sendto(encrypt(('GET %s ' % query).encode('ascii')), ('127.0.0.1', 2080))
                    try:
                        packet, address = channel.recvfrom(8192)
                    except socket.timeout:
                        continue
                    if address != ('127.0.0.1', 2080):
                        continue
                    response = decrypt(packet)
                    kind = response.split(' ', 1)[0]
                    if kind in ('AUT', 'ADP', 'VER'):
                        responses[kind] = response
                if len(responses) != 3:
                    raise RuntimeError('后台未在 15 秒内完整响应认证类型、网卡和版本查询。')
                for kind in ('AUT', 'ADP', 'VER'):
                    print('[通过] 本地通信：' + responses[kind], flush=True)
                time.sleep(2)
                if process.poll() is not None:
                    raise RuntimeError('认证后台在初始化后退出：%s' % process.returncode)
                print('[通过] 后台完成初始化且持续存活；未发送账号密码或发起登录。', flush=True)
            except Exception:
                log.seek(0)
                print(log.read().decode('gb18030', errors='replace')[-12000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-test', action='store_true')
    options = parser.parse_args()
    if package_check():
        return 1
    if options.service_test:
        try:
            service_test()
        except (OSError, RuntimeError, ValueError) as error:
            print('[失败] ' + str(error), file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
