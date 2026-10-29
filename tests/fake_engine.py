#!/usr/bin/python3
"""Independent test process: no campus network traffic, only synthetic accounts."""
import argparse
import base64
import json
from pathlib import Path
import signal
import socket
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import decrypt, encrypt

parser = argparse.ArgumentParser()
parser.add_argument('-p', type=int, required=True)
parser.add_argument('-f')
parser.add_argument('-l')
options = parser.parse_args()
sys.excepthook = lambda kind, value, tb: (Path(options.f) / 'failure.txt').write_text(
    ''.join(traceback.format_exception(kind, value, tb)))
selected, account = '', ''
channel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
channel.bind(('127.0.0.1', options.p))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    packet, peer = channel.recvfrom(8192)
    words = decrypt(packet).split()
    command = words[0]
    with (Path(options.f) / 'commands.jsonl').open('a') as trace:
        trace.write(json.dumps({'command': command, 'argument': words[1] if command == 'SET' else ''}) + '\n')
    response = 'SUC '
    if words[:2] == ['GET', 'AUT']:
        response = 'AUT 00000001'
    elif words[:2] == ['GET', 'ADP']:
        response = 'ADP 1 IP:192.0.2.20|GW:|MAC:020000000001@enp-test'
    elif words[:2] == ['GET', 'VER']:
        response = 'VER integration-fixture'
    elif words[:2] == ['SET', 'ADP']:
        selected = words[2]
    elif command == 'LGI':
        account, password = (base64.b64decode(word, validate=True).decode() for word in words[1:3])
        if selected != 'enp-test' or password != 'test-password':
            response = 'LGI ERR 测试凭据或网卡选择不正确'
        elif account == 'reject':
            response = 'LGI ERR 账号或密码错误'
        elif account == 'attributes-timeout':
            channel.sendto(encrypt('LGI STM 802.1x 认证成功！'.encode().ljust(32, b'\0')), peer)
            response = 'LGI ERR 获取用户属性超时！'
        elif account in ('hang', 'cancel'):
            response = 'LGI STM 正在等待测试服务器'
        elif account == 'crash':
            sys.exit(23)
        else:
            channel.sendto(b'invalid', peer)
            response = 'LGI OK 校园网认证成功'
    elif command == 'LGO':
        if account == 'logout-hang':
            continue
        response = 'LGO OK 已退出'
    channel.sendto(encrypt(response.encode('utf-8').ljust(32, b'\0')), peer)
