"""Network preflight and the complete lifecycle of one Dr.COM session."""

import base64
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import select
import socket
import stat
import subprocess
import threading
import time

from protocol import decrypt, encrypt

APP_ROOT = Path(__file__).resolve().parent
PACKAGED = APP_ROOT == Path('/usr/lib/mydrclient')
ENGINE = APP_ROOT / 'engine' if PACKAGED else Path('/usr/local/lib/mydrclient')
INSTALL_HINT = '请重新安装 MyDrClient 安装包。' if PACKAGED else '请运行项目目录中的 privillege.sh。'
ENGINE_HASH = 'ae8bd0b11a3606c8e85f47bf5e914a09e515c75cf83e333e8b722ee6952ef40c'


@dataclass(frozen=True)
class Adapter:
    name: str
    kind: str
    carrier: bool
    ipv4: str
    mac: str

    def issue(self):
        if not self.carrier:
            return '尚未连接网络，请检查网线或无线连接。'
        if not self.ipv4:
            return '此网卡尚无 IPv4 地址，请先在系统网络设置中配置校园网连接。'
        return ''


def adapters():
    """Physical interfaces only; never change the user's network configuration."""
    data = subprocess.check_output(['/usr/sbin/ip', '-j', 'address', 'show'],
                                   timeout=3, text=True)
    result = []
    for item in json.loads(data):
        name = item['ifname']
        path = Path('/sys/class/net') / name
        if not (path / 'device').exists() or item.get('link_type') != 'ether':
            continue
        addresses = [entry['local'] for entry in item.get('addr_info', [])
                     if entry['family'] == 'inet' and not ipaddress.ip_address(entry['local']).is_link_local]
        result.append(Adapter(name, '无线' if (path / 'wireless').exists() else '有线',
                              'LOWER_UP' in item.get('flags', []),
                              addresses[0] if addresses else '', item.get('address', '')))
    return sorted(result, key=lambda item: (bool(item.issue()), item.kind != '有线', item.name))


class Client:
    """One worker owns the socket, credentials, child process and all state changes."""

    def __init__(self, notify, *, directory=ENGINE, port=2080, network=adapters,
                 privileged=True, startup_timeout=12, login_timeout=40):
        self.notify = notify
        self.directory, self.port = Path(directory), port
        self.network, self.privileged = network, privileged
        self.startup_timeout, self.login_timeout = startup_timeout, login_timeout
        self.state, self.account, self.interface = 'idle', '', ''
        self.process, self.channel = None, None
        self.deadline, self.last_query, self.last_link_check = 0, 0, 0
        self.seen, self.redactions = {}, []
        self.commands = queue.Queue()
        self.closing = threading.Event()
        self.thread = threading.Thread(target=self._run, name='mydrclient-session', daemon=True)
        self.thread.start()

    def login(self, interface, account, password):
        self.commands.put(('login', interface, account, password))

    def disconnect(self):
        self.commands.put(('disconnect',))

    def cancel(self):
        self.commands.put(('cancel',))

    def close(self):
        self.closing.set()

    def _emit(self, kind, message):
        for token in self.redactions:
            if token:
                message = message.replace(token, '•••')
        self.notify(kind, message[:400])

    def _state(self, state, message):
        self.state = state
        self._emit('state', state)
        self._emit('message', message)

    def _send(self, text):
        self.channel.sendto(encrypt(text.encode('utf-8')), ('127.0.0.1', self.port))

    def _start(self, interface, account, password):
        if self.state not in ('idle', 'error'):
            return
        account = account.strip()
        if not account or not password:
            raise ValueError('请填写校园网账号和密码。')
        if any(character.isspace() for character in account) or '\0' in account + password:
            raise ValueError('账号不能包含空白字符，账号和密码不能包含空字符。')
        if max(len(account.encode()), len(password.encode())) > 35:
            raise ValueError('原认证后台最多支持 35 个 UTF-8 字节的账号或密码。')
        self.account, self.interface = account, interface
        self.redactions = [password, account, base64.b64encode(password.encode()).decode(),
                           base64.b64encode(account.encode()).decode()]
        self._state('starting', '正在检查网络和认证服务…')
        adapter = next((item for item in self.network() if item.name == interface), None)
        if adapter is None:
            raise ValueError('所选网卡已消失，请刷新网卡列表。')
        if adapter.issue():
            raise ValueError(adapter.issue())
        executable = self.directory / 'drcomauthsvr'
        if not executable.is_file():
            raise ValueError('认证服务尚未安装，' + INSTALL_HINT)
        if self.privileged:
            info = executable.stat()
            parent = self.directory.stat()
            if (info.st_uid != 0 or not info.st_mode & stat.S_ISUID or
                    parent.st_uid != 0 or parent.st_mode & 0o022 or
                    hashlib.sha256(executable.read_bytes()).hexdigest() != ENGINE_HASH):
                raise ValueError('认证服务的版本或权限不正确，' + INSTALL_HINT)
        ports = [('127.0.0.1', self.port)]
        if self.privileged:
            ports.append(('0.0.0.0', 61440))
        for address in ports:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.bind(address)
                except OSError as error:
                    raise ValueError('已有认证客户端在运行，请先退出旧客户端，再连接。') from error
        self.channel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.channel.bind(('127.0.0.1', 0))
        self.channel.setblocking(False)
        self.process = subprocess.Popen(
            [str(executable), '-f', str(self.directory), '-p', str(self.port), '-l', '1'],
            cwd=self.directory, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'zh_CN.UTF-8'},
            start_new_session=True)
        self.seen = {}
        self.credentials = (base64.b64encode(account.encode()).decode(),
                            base64.b64encode(password.encode()).decode())
        self.deadline, self.last_query = time.monotonic() + self.startup_timeout, 0

    def _packet(self, text):
        kind, _, value = text.strip().partition(' ')
        if self.state == 'starting' and kind in ('AUT', 'ADP', 'VER'):
            self.seen[kind] = value
            if len(self.seen) == 3:
                if self.seen['AUT'] not in ('00000001', '00000002'):
                    raise ValueError('此认证模式尚未支持；第一版支持当前校园网使用的普通认证和 802.1X。')
                names = [entry.rsplit('@', 1)[-1] for entry in self.seen['ADP'].split() if '@' in entry]
                if self.interface not in names:
                    raise ValueError('认证服务未识别到所选网卡，请重新连接网线后重试。')
                self._send('SET ADP ' + self.interface + ' ')
                self._send('LGI %s %s' % self.credentials)
                self.credentials = None
                self.deadline = time.monotonic() + self.login_timeout
                self._state('authenticating', '正在向校园网认证，请稍候…')
        elif kind == 'LGI' and self.state in ('authenticating', 'online'):
            status, _, message = value.partition(' ')
            if status == 'OK' and self.state == 'authenticating':
                self.deadline = 0
                self._state('online', '校园网认证成功。')
            elif status == 'ERR':
                if '属性' in message and '超时' in message:
                    message += ' 若同时连接 Wi-Fi，请检查认证服务器的 UDP 61440 是否仍从所选有线网卡发送。'
                raise ValueError(message or '服务器拒绝了认证，请核对账号和网络设置。')
            elif status in ('STM', '81X') and self.state == 'authenticating':
                self._emit('message', message or '正在完成 802.1X 握手…')
        elif kind == 'LGO':
            status, _, message = value.partition(' ')
            if self.state == 'disconnecting' and status == 'OK':
                self._stop()
                self._state('idle', '已断开校园网连接。')
            elif self.state == 'disconnecting' and status == 'ERR':
                raise ValueError(message or '服务器未能确认退出，已停止本地认证进程。')
            elif self.state == 'online':
                raise ValueError(message or '校园网连接已断开，请重新登录。')
        elif kind == 'ERR' and self.state in ('starting', 'authenticating'):
            raise ValueError(value or '认证服务返回错误。')
        elif kind == 'STM' and self.state == 'authenticating':
            self._emit('message', value)

    def _stop(self):
        if self.channel:
            if self.state in ('authenticating', 'online') and self.account:
                try:
                    self._send('LGO ' + self.account)
                    readable, _, _ = select.select([self.channel], [], [], 0.3)
                    if readable:
                        self.channel.recv(8192)
                except OSError:
                    pass
            self.channel.close()
            self.channel = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        self.credentials = None
        self.deadline = 0

    def _run(self):
        try:
            while not self.closing.is_set():
                try:
                    command = self.commands.get(timeout=0.05 if self.channel else 0.2)
                except queue.Empty:
                    command = None
                try:
                    if command and command[0] == 'login':
                        self._start(*command[1:])
                    elif command and command[0] == 'cancel':
                        self._stop()
                        self._state('idle', '已取消本次连接。')
                    elif command and command[0] == 'disconnect' and self.state == 'online':
                        self._send('LGO ' + self.account)
                        self.deadline = time.monotonic() + 8
                        self._state('disconnecting', '正在退出校园网认证…')
                    if not self.channel:
                        continue
                    if self.process.poll() is not None:
                        raise ValueError('认证后台意外退出，请重新连接；反复出现时请查看诊断记录。')
                    now = time.monotonic()
                    if self.privileged and self.state in ('authenticating', 'online') and now - self.last_link_check >= 1:
                        path = Path('/sys/class/net') / self.interface
                        if not path.exists() or (path / 'carrier').read_text().strip() != '1':
                            raise ValueError('网络链路已断开，请检查网线或无线连接。')
                        self.last_link_check = now
                    if self.state == 'starting' and now - self.last_query >= 0.6:
                        for query in ('AUT', 'ADP', 'VER'):
                            self._send('GET ' + query + ' ')
                        self.last_query = now
                    readable, _, _ = select.select([self.channel], [], [], 0.1)
                    if readable:
                        packet, address = self.channel.recvfrom(8192)
                        if address == ('127.0.0.1', self.port):
                            try:
                                text = decrypt(packet)
                            except (UnicodeError, ValueError):
                                continue
                            self._packet(text)
                    if self.deadline and now >= self.deadline:
                        if self.state == 'starting':
                            raise ValueError('认证服务启动超时，请检查首次配置或关闭其他认证客户端。')
                        if self.state == 'disconnecting':
                            raise ValueError('服务器未确认退出，已停止本地认证进程。')
                        raise ValueError('认证等待超时，请检查有线 IPv4、网卡选择和校园网服务后重试。')
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    self._stop()
                    self._state('error', str(error))
        finally:
            self._stop()
