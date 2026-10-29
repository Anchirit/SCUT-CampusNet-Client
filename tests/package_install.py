#!/usr/bin/python3
"""Install, run, upgrade and remove the actual .deb in private namespaces."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def audit(package):
    data = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        entries = archive.getmembers()
        assert all(item.uid == item.gid == 0 for item in entries)
        assert all(not (item.mode & 0o022) for item in entries)
        assert all(item.name in ('.', './usr') or item.name.startswith('./usr/') for item in entries)
        files = {item.name: item for item in entries if item.isfile()}
        daemon = files['./usr/lib/mydrclient/engine/drcomauthsvr']
        assert daemon.mode == 0o4755
        assert [name for name, item in files.items() if item.mode & 0o6000] == [daemon.name]
        assert './usr/bin/mydrclient' in files
        assert './usr/share/applications/io.github.Anchirit.MyDrClient.desktop' in files
        assert not any('test-results' in name or '__pycache__' in name or 'settings.json' in name for name in files)
        assert not any(item.issym() or item.islnk() for item in entries)
        assert os.fsencode(Path.home()) + b'/' not in data
        expected = json.loads((ROOT / 'vendor/SHA256SUMS.json').read_text())
        for name, digest in expected.items():
            content = archive.extractfile('./usr/lib/mydrclient/engine/' + name).read()
            assert hashlib.sha256(content).hexdigest() == digest
    print('[通过] 安装包清单、root 所有权、唯一 SetUID 后台、后台校验及个人路径检查', flush=True)


def isolated(package, temporary):
    assert os.geteuid() == 0 and os.getpid() == 1, '只允许在测试创建的 PID / 用户命名空间内安装。'
    sys.dont_write_bytecode = True
    run('mount', '--make-rprivate', '/')

    # Every directory receiving package files is private. Existing system files
    # remain readable through links to bind-mounted originals. The parent removes
    # this directory only AFTER this namespace and all its mounts have vanished.
    branches = {
        'bin': {'mydrclient': None},
        'lib': {'mydrclient': None},
        'share': {
            'applications': {'io.github.Anchirit.MyDrClient.desktop': None},
            'icons': {'hicolor': {'scalable': {'apps': {'mydrclient.svg': None}}}},
            'doc': {'mydrclient': None},
        },
    }

    def shadow(original, destination, overrides):
        destination.mkdir()
        for item in original.iterdir():
            if item.name not in overrides:
                (destination / item.name).symlink_to(item)
        for name, children in overrides.items():
            if children is not None:
                shadow(original / name, destination / name, children)

    for name, overrides in branches.items():
        original = temporary / ('original-' + name)
        original.mkdir()
        run('mount', '--bind', '/usr/' + name, str(original))
        destination = temporary / ('private-' + name)
        shadow(original, destination, overrides)
        run('mount', '--bind', str(destination), '/usr/' + name)
    run('mount', '-t', 'sysfs', 'sysfs', '/sys')
    admin = temporary / 'dpkg'
    admin.mkdir()
    for name in ('info', 'updates', 'triggers'):
        (admin / name).mkdir()
    status = Path('/var/lib/dpkg/status').read_text()
    (admin / 'status').write_text('\n\n'.join(
        entry for entry in status.split('\n\n') if not entry.startswith('Package: mydrclient\n')))
    packages = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\n'], text=True)
    for name in packages.splitlines():
        if name != 'mydrclient':
            (admin / 'info' / (name + '.list')).touch()
    # Shared system directories normally belong to preinstalled packages. Record
    # those shared owners so dpkg never attempts to remove namespace mountpoints.
    shared = ['/.', '/usr']
    def shared_directories(prefix, children):
        shared.append(prefix)
        for name, descendants in children.items():
            if descendants is not None:
                shared_directories(prefix + '/' + name, descendants)
    for name, children in branches.items():
        shared_directories('/usr/' + name, children)
    (admin / 'info/base-files.list').write_text('\n'.join(shared) + '\n')
    dpkg = ['dpkg', '--admindir=' + str(admin), '--log=' + str(temporary / 'dpkg.log'),
            '--force-script-chrootless', '--no-triggers']
    run(*dpkg, '--install', str(package))
    app = Path('/usr/lib/mydrclient')
    daemon = app / 'engine/drcomauthsvr'
    info = daemon.stat()
    assert info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o4755
    assert not app.stat().st_mode & 0o022
    assert subprocess.check_output(['/usr/bin/mydrclient', '--version'], text=True).strip() == 'MyDrClient ' + (ROOT / 'VERSION').read_text().strip()
    run('desktop-file-validate', '/usr/share/applications/io.github.Anchirit.MyDrClient.desktop')
    print('[通过] 实际 dpkg 安装、依赖解析、启动入口、权限及应用菜单文件', flush=True)

    # A private bus avoids activating or changing any application on the host bus.
    bus_config = temporary / 'bus.conf'
    bus_config.write_text('<busconfig><type>session</type><listen>unix:path=' +
        str(temporary / 'bus') + '</listen><auth>EXTERNAL</auth><policy context="default">'
        '<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>'
        '</policy></busconfig>')
    # No service directories: the private bus must not auto-start desktop portals,
    # keyrings or other services from the host session.
    bus = subprocess.Popen(['/usr/bin/dbus-daemon', '--config-file=' + str(bus_config), '--nofork', '--print-address=1',
                            '--nopidfile'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    environment = os.environ.copy()
    environment.update(DBUS_SESSION_BUS_ADDRESS=bus.stdout.readline().strip(),
                       XDG_CONFIG_HOME=str(temporary / 'config'), XDG_STATE_HOME=str(temporary / 'state'),
                       XDG_CACHE_HOME=str(temporary / 'cache'), GSK_RENDERER='cairo',
                       GTK_A11Y='none', NO_AT_BRIDGE='1', PYTHONDONTWRITEBYTECODE='1',
                       GTK_IM_MODULE='gtk-im-context-simple', XMODIFIERS='@im=none')
    environment['DBUS_SYSTEM_BUS_ADDRESS'] = environment['DBUS_SESSION_BUS_ADDRESS']
    environment.pop('SESSION_MANAGER', None)
    application = None
    gui_log = (temporary / 'gtk.log').open('w')
    try:
        # Test the installed native executable, rather than importing the source checkout.
        application = subprocess.Popen(['/usr/bin/mydrclient'], env=environment,
                                       stdout=gui_log, stderr=gui_log, text=True)
        quit_command = ['/usr/bin/gapplication', 'action', 'io.github.Anchirit.MyDrClient', 'quit']
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert application.poll() is None, (temporary / 'gtk.log').read_text()
            probe = subprocess.run(['/usr/bin/gdbus', 'call', '--session', '--timeout', '2', '--dest',
                'io.github.Anchirit.MyDrClient', '--object-path', '/io/github/Anchirit/MyDrClient',
                '--method', 'org.gtk.Actions.List'], env=environment, capture_output=True, text=True)
            if probe.returncode == 0 and "'quit'" in probe.stdout:
                break
            time.sleep(0.1)
        else:
            raise AssertionError('安装后的实际图形入口没有注册应用。' + probe.stderr +
                                 (temporary / 'gtk.log').read_text())
        assert subprocess.run(['/usr/bin/mydrclient'], env=environment, timeout=5).returncode == 0
        assert application.poll() is None
        run(*quit_command, env=environment, capture_output=True)
        application.wait(timeout=8)
        log = (temporary / 'gtk.log').read_text()
        assert application.returncode == 0, log
        assert 'Traceback' not in log, log
        print('[通过] 安装后的原生入口启动 GTK、单实例再次打开、菜单退出与进程回收', flush=True)
    finally:
        if application and application.poll() is None:
            application.kill()
            application.wait(timeout=5)
        bus.terminate()
        bus.wait(timeout=5)
        gui_log.close()

    for command in (['ip', 'link', 'set', 'lo', 'up'],
                    ['ip', 'link', 'add', 'enp-test', 'type', 'dummy'],
                    ['ip', 'link', 'set', 'enp-test', 'up'],
                    ['ip', 'addr', 'add', '192.0.2.20/24', 'dev', 'enp-test']):
        run(*command)
    sys.path.insert(0, str(app))
    from client import Adapter, Client, ENGINE
    assert ENGINE == app / 'engine'
    events = []
    client = Client(lambda kind, text: events.append((kind, text)),
                    network=lambda: [Adapter('enp-test', '有线', True, '192.0.2.20', '02:00:00:00:00:01')],
                    login_timeout=8)
    try:
        client.login('enp-test', 'isolated-test', 'test-password')
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline and client.state not in ('authenticating', 'error'):
            time.sleep(0.03)
        assert client.state == 'authenticating', events
        assert set(client.seen) == {'AUT', 'ADP', 'VER'}, client.seen
        pid = client.process.pid
        for arguments in (['--install', str(package)], ['--remove', 'mydrclient']):
            blocked = subprocess.run(dpkg + arguments, capture_output=True, text=True)
            assert blocked.returncode != 0 and '退出程序' in blocked.stderr, blocked.stdout + blocked.stderr
            assert client.process is not None and client.process.pid == pid and client.process.poll() is None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and client.state != 'error':
            time.sleep(0.03)
        assert client.state == 'error' and any('超时' in message for _, message in events), events
        assert client.process is None and client.channel is None
        assert all('test-password' not in message and 'isolated-test' not in message for _, message in events)
    finally:
        client.close()
        client.thread.join(timeout=5)
        assert not client.thread.is_alive()
    print('[通过] 已安装真实后台握手、登录请求、超时清理；在线升级/卸载受到保护', flush=True)
    # Reinstall/upgrade completes once the client has exited; preferences survive removal.
    run(*dpkg, '--install', str(package))
    preferences = temporary / 'config/mydrclient/settings.json'
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_text('{"background":true}\n')
    run(*dpkg, '--remove', 'mydrclient')
    assert not Path('/usr/bin/mydrclient').exists() and not app.exists()
    assert not Path('/usr/share/applications/io.github.Anchirit.MyDrClient.desktop').exists()
    assert not Path('/usr/share/icons/hicolor/scalable/apps/mydrclient.svg').exists()
    assert preferences.exists()
    print('[通过] 退出后重装/升级、完整卸载及用户偏好保留', flush=True)


def main():
    package = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'dist/mydrclient_1.0.0_amd64.deb'
    if len(sys.argv) > 2 and sys.argv[2] == '--isolated':
        isolated(package, Path(sys.argv[3]))
        return
    audit(package)
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='deb-namespace-', dir=output) as temporary:
        result = subprocess.run(['unshare', '--user', '--map-root-user', '--net', '--mount',
            '--pid', '--fork', '--kill-child=KILL', '--mount-proc', '/usr/bin/python3', str(__file__), str(package),
            '--isolated', temporary], capture_output=True, text=True, timeout=100)
        print(result.stdout, end='')
        (output / 'package-install.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr[-6000:]
    (output / 'package-report.json').write_text(json.dumps({
        'package': package.name, 'sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
        'passed': True, 'host_installation': False,
        'scope': '实际 dpkg 安装、原生 GTK 入口、单实例、真实后台、升级保护、重装、卸载；网络完全隔离'
    }, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
