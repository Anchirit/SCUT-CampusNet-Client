"""The original client's local UDP protocol, used only for read-only probes."""

import struct

KEY = struct.unpack('<4I', b'?CrD?CrD?CrD?CrD')
DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF


def _mix(z, y, total, index, e):
    return (((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4)) ^
            ((total ^ y) + (KEY[(index & 3) ^ e] ^ z))) & MASK


def encrypt(message):
    message += b'\0' * (-len(message) % 4)
    count = len(message) // 4
    if count < 2:
        raise ValueError('local protocol requires at least two words')
    words = list(struct.unpack('<%dI' % count, message))
    z, total = words[-1], 0
    for _ in range(6 + 52 // count):
        total = (total + DELTA) & MASK
        e = (total >> 2) & 3
        for index in range(count):
            y = words[(index + 1) % count]
            words[index] = (words[index] + _mix(z, y, total, index, e)) & MASK
            z = words[index]
    return struct.pack('<%dI' % count, *words)


def decrypt(message):
    if len(message) < 8 or len(message) % 4:
        raise ValueError('invalid local protocol response size')
    count = len(message) // 4
    words = list(struct.unpack('<%dI' % count, message))
    y = words[0]
    total = ((6 + 52 // count) * DELTA) & MASK
    while total:
        e = (total >> 2) & 3
        for index in range(count - 1, -1, -1):
            z = words[(index - 1) % count]
            words[index] = (words[index] - _mix(z, y, total, index, e)) & MASK
            y = words[index]
        total = (total - DELTA) & MASK
    return struct.pack('<%dI' % count, *words).split(b'\0', 1)[0].decode('gb18030', errors='replace')
