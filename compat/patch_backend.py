#!/usr/bin/python3
"""Apply a version-checked null-pointer fix to the supplied Dr.COM backend."""

import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = '3dc2ca238c0acd1a562c767f2c8db38457b1ec4f2d0fb95832091c2c55b720ea'
ADDRESS = 0x080662D6
LENGTH = 54


def main():
    original = (ROOT / 'originals/drcomauthsvr').read_bytes()
    if hashlib.sha256(original).hexdigest() != EXPECTED:
        raise SystemExit('不支持的认证后台版本，未修改任何程序。')
    if original[:5] != b'\x7fELF\x01':
        raise SystemExit('认证后台不是预期的 ELF32 程序。')
    phoff = struct.unpack_from('<I', original, 28)[0]
    phsize, count = struct.unpack_from('<HH', original, 42)
    offset = None
    for index in range(count):
        kind, pos, address, _, size = struct.unpack_from('<5I', original, phoff + phsize * index)
        if kind == 1 and address <= ADDRESS and ADDRESS + LENGTH <= address + size:
            offset = pos + ADDRESS - address
            break
    if offset is None:
        raise SystemExit('无法定位补丁位置，未修改任何程序。')
    with tempfile.TemporaryDirectory(prefix='drclient-patch-') as tmp:
        tmp = Path(tmp)
        subprocess.run(['as', '--32', str(ROOT / 'compat/null_address.s'), '-o', str(tmp / 'fix.o')], check=True)
        subprocess.run(['ld', '-m', 'elf_i386', '-Ttext', hex(ADDRESS), str(tmp / 'fix.o'), '-o', str(tmp / 'fix.elf')], check=True)
        subprocess.run(['objcopy', '-O', 'binary', '--only-section=.text', str(tmp / 'fix.elf'), str(tmp / 'fix.bin')], check=True)
        patch = (tmp / 'fix.bin').read_bytes()
    if len(patch) != LENGTH:
        raise SystemExit('补丁长度不符合预期，未修改任何程序。')
    updated = bytearray(original)
    updated[offset:offset + LENGTH] = patch
    target = ROOT / 'drcomauthsvr'
    if target.exists() and target.read_bytes() == updated:
        print('认证后台补丁已经存在。')
        return
    temporary = ROOT / '.drcomauthsvr.new'
    temporary.write_bytes(updated)
    temporary.chmod(0o755)
    temporary.replace(target)
    print('已修复网卡地址和子网掩码为空时的崩溃。')


if __name__ == '__main__':
    main()
