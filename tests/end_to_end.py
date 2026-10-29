#!/usr/bin/python3
"""One complete integration run: GUI workflows, isolated real engine, keyring."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def isolated_engine():
    from client import Adapter, Client
    # Exercise the installer in a private mount namespace, never the host system directory.
    staging = tempfile.TemporaryDirectory(prefix='mydrclient-system-', dir=ROOT / 'test-results')
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    subprocess.run(['mount', '--bind', staging.name, '/usr/local/lib'], check=True)
    subprocess.run(['mount', '-t', 'sysfs', 'sysfs', '/sys'], check=True)
    subprocess.run(['/usr/bin/python3', str(ROOT / 'install.py')], check=True)
    for command in (['ip', 'link', 'set', 'lo', 'up'],
                    ['ip', 'link', 'add', 'enp-test', 'type', 'dummy'],
                    ['ip', 'link', 'set', 'enp-test', 'up'],
                    ['ip', 'addr', 'add', '192.0.2.20/24', 'dev', 'enp-test']):
        subprocess.run(command, check=True)
    items = [Adapter('enp-test', '有线', True, '192.0.2.20', '02:00:00:00:00:01')]
    events = []
    client = Client(lambda kind, text: events.append((kind, text)),
                    network=lambda: items, privileged=True, startup_timeout=12, login_timeout=2)
    try:
        client.login('enp-test', 'isolated-test', 'test-password')
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline and client.state != 'error':
            time.sleep(0.05)
        assert set(client.seen) == {'AUT', 'ADP', 'VER'}, client.seen
        assert any(pair == ('state', 'authenticating') for pair in events), events
        assert client.state == 'error' and any('超时' in message for _, message in events), events
        assert client.process is None and client.channel is None
        assert all('test-password' not in message and 'isolated-test' not in message for _, message in events)
        print('真实后台：隔离安装、权限校验、初始化、查询、网卡设置、登录请求、超时及清理通过。')
    finally:
        client.close()
        client.thread.join(timeout=5)
        assert not client.thread.is_alive()
        staging.cleanup()


def run():
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mydrclient-e2e-', dir=output) as temporary:
        temporary = Path(temporary)
        os.environ['XDG_CONFIG_HOME'] = str(temporary / 'config')
        os.environ['XDG_STATE_HOME'] = str(temporary / 'state')
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Gdk', '4.0')
        from gi.repository import Gdk, Gio, GLib, Gtk
        import storage
        from client import Adapter, Client, adapters
        from app import Application
        from tray import Tray, WATCHER, WATCHER_PATH

        results = []
        def passed(text):
            results.append(text)
            print('[通过] ' + text, flush=True)

        physical = adapters()
        expected_interfaces = {path.name for path in Path('/sys/class/net').iterdir()
                               if (path / 'device').exists() and (path / 'type').read_text().strip() == '1'}
        assert {item.name for item in physical} == expected_interfaces
        assert all(item.mac for item in physical)
        passed('本机实际网卡枚举与 sysfs 一致，IPv4 和 MAC 读取正常且排除虚拟接口')

        def until(predicate, timeout=5):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                if predicate():
                    return
                time.sleep(0.015)
            failure = engine / 'failure.txt'
            detail = failure.read_text() if failure.exists() else ''
            raise AssertionError('等待条件超时：' + window.state + ' / ' + window.message.get_text() + '\n' + detail)

        # Keep fixture secrets in memory; test the real desktop keyring separately below.
        real_keyring = storage.Keyring
        class MemoryKeyring:
            values = {}
            from gi.repository import Gio
            def __init__(self): self.cancel = self.Gio.Cancellable()
            def lookup(self, account, callback): callback(self.values.get(account, ''), '')
            def store(self, account, password, callback): self.values[account] = password; callback('')
            def clear(self, account, callback): self.values.pop(account, None); callback('')
        storage.Keyring = MemoryKeyring

        engine = temporary / 'engine'
        engine.mkdir()
        (engine / 'drcomauthsvr').symlink_to(ROOT / 'tests/fake_engine.py')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        wired = Adapter('enp-test', '有线', True, '192.0.2.20', '02:00:00:00:00:01')
        items = [wired, Adapter('wlp-test', '无线', True, '192.0.2.30', '02:00:00:00:00:02')]
        application = Application(
            client_factory=lambda callback: Client(callback, directory=engine, port=port,
                network=lambda: items, privileged=False, startup_timeout=3, login_timeout=1),
            network_loader=lambda: list(items),
            application_id='io.github.Anchirit.MyDrClient.Test' + uuid.uuid4().hex)
        assert application.register(None)
        application.activate()
        window = application.window
        window.set_title('MyDrClient · 集成验证（模拟认证）')
        until(lambda: len(window.items) == 2)
        assert window.selected().name == 'enp-test'
        assert '192.0.2.20' in window.network_info.get_text()
        assert Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon('mydrclient')
        assert 'gnome-control-center' not in (ROOT / 'app.py').read_text()
        passed('GTK 窗口启动、物理网卡列表、默认有线选择及网络详情')

        tray = application.tray
        until(lambda: tray.available)
        bus = application.get_dbus_connection()
        def call(interface, method, signature, args, path='/MenuBar', destination=None):
            complete = []
            def done(connection, result):
                try:
                    complete.append(connection.call_finish(result))
                except GLib.Error as error:
                    complete.append(error)
            bus.call(destination or tray.name, path, interface, method, GLib.Variant(signature, args),
                     None, Gio.DBusCallFlags.NONE, 2500, None, done)
            until(lambda: bool(complete))
            if isinstance(complete[0], GLib.Error):
                raise complete[0]
            return complete[0].unpack()
        def menu_event(item):
            call('com.canonical.dbusmenu', 'Event', '(isvu)', (item, 'clicked', GLib.Variant('i', 0), 0))
        def item_properties():
            return call('org.freedesktop.DBus.Properties', 'GetAll', '(s)',
                        ('org.kde.StatusNotifierItem',), '/StatusNotifierItem')[0]
        registered = call('org.freedesktop.DBus.Properties', 'Get', '(ss)',
                          (WATCHER, 'RegisteredStatusNotifierItems'), WATCHER_PATH, WATCHER)[0]
        assert any(name.split('/')[0] == tray.name for name in registered)
        props = item_properties()
        assert Path(props['IconName']).is_file()
        assert props['Menu'] == '/MenuBar'
        assert all(len(pixels) == width * height * 4 for width, height, pixels in props['IconPixmap'])
        layout = call('com.canonical.dbusmenu', 'GetLayout', '(iias)', (0, -1, []))[1]
        menu_items = {item[0]: item[1] for item in layout[2]}
        assert menu_items[4]['toggle-state'] == 1 and not menu_items[3]['enabled']
        assert props['IconName'].endswith('-idle.svg')
        passed('实际桌面托盘注册、图标像素、DBusMenu 布局和默认关闭选项')
        if Gdk.Display.get_default().__class__.__name__ == 'X11Display':
            gi.require_version('GdkX11', '4.0')
            from gi.repository import GdkX11
            painted = []
            GLib.timeout_add(150, lambda: (painted.append(True), False)[1])
            until(lambda: bool(painted))
            subprocess.run(['/usr/bin/python3', str(ROOT / 'tests/capture.py'),
                            str(window.get_surface().get_xid()), str(output / 'preview.png')], check=True)
            window.close()
            until(lambda: not window.get_visible())
            desktop_menu = subprocess.Popen(['/usr/bin/python3', str(ROOT / 'tests/tray_ui.py'), str(output)],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            until(lambda: desktop_menu.poll() is not None, timeout=18)
            menu_output, menu_error = desktop_menu.communicate()
            assert desktop_menu.returncode == 0, menu_output + menu_error
            until(window.get_visible)
            passed('GNOME 右上角实际图标及菜单可见，从桌面菜单成功恢复隐藏窗口')

        def attempt(account):
            window.account.set_text(account)
            window.password.set_text('test-password')
            window.action.emit('clicked')

        window.remember.set_active(True)
        attempt('end-to-end')
        until(lambda: window.state == 'online')
        assert window.status.get_text() == '已连接'
        assert not window.account.get_sensitive()
        assert MemoryKeyring.values['end-to-end'] == 'test-password'
        assert window.action.get_label() == '断开连接'
        assert item_properties()['IconName'].endswith('-online.svg')
        passed('通过真实窗口操作完成握手、认证成功、按钮状态与密码存储调用')

        pid = window.client.process.pid
        window.close()
        until(lambda: not window.get_visible())
        assert window.state == 'online' and window.client.process.pid == pid
        call('org.kde.StatusNotifierItem', 'Activate', '(ii)', (0, 0), '/StatusNotifierItem')
        until(window.get_visible)
        window.close()
        until(lambda: not window.get_visible())
        remote = subprocess.Popen(['/usr/bin/python3', '-c',
            'import sys; from gi.repository import Gio; app=Gio.Application(application_id=sys.argv[1]); '
            'assert app.register(None) and app.get_is_remote(); app.activate()', application.get_application_id()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        until(lambda: remote.poll() is not None)
        assert remote.returncode == 0, remote.communicate()
        until(window.get_visible)
        assert application.window is window and window.client.process.pid == pid
        passed('在线关闭保持同一认证进程；托盘点击及独立进程单实例激活均能恢复窗口')
        menu_event(4)
        until(lambda: not application.background_action.get_state().unpack())
        assert storage.load()['background'] is False
        menu_event(4)
        until(lambda: application.background_action.get_state().unpack())
        assert storage.load()['background'] is True
        menu_event(3)
        until(lambda: window.state == 'idle')
        assert window.client.process is None and window.client.channel is None
        passed('退出认证得到确认，后台进程与通信端口释放')

        window.remember.set_active(False)
        for account, expected in (('reject', '账号或密码错误'), ('attributes-timeout', 'UDP 61440'),
                                  ('hang', '超时'), ('crash', '意外退出')):
            event_start = (storage.STATE / 'client.log').stat().st_size
            attempt(account)
            until(lambda: window.state == 'error')
            assert expected in window.message.get_text(), window.message.get_text()
            assert window.account.get_sensitive() and window.action.get_sensitive()
            assert window.client.process is None
            assert item_properties()['IconName'].endswith('-error.svg')
            if account == 'attributes-timeout':
                with (storage.STATE / 'client.log').open() as records:
                    records.seek(event_start)
                    assert not any(json.loads(line) == {'event': 'state', 'message': 'online'} for line in records)
        passed('服务器拒绝、用户属性超时、认证超时和后台崩溃均明确报错；802.1X 成功不冒充最终成功')

        attempt('cancel')
        until(lambda: window.state == 'authenticating')
        assert item_properties()['IconName'].endswith('-busy.svg')
        menu_event(3)
        until(lambda: window.state == 'idle')
        assert window.client.process is None
        passed('认证中取消，停止后台并恢复输入')

        for item, expected in ((Adapter('enp-test', '有线', False, '', wired.mac), '检查网线'),
                               (Adapter('enp-test', '有线', True, '', wired.mac), 'IPv4')):
            items[:] = [item]
            window.scan()
            until(lambda: window.items == items)
            attempt('preflight')
            until(lambda: window.state == 'error')
            assert expected in window.message.get_text(), window.message.get_text()
            assert window.client.process is None
        passed('网线断开或缺少 IPv4 时，在启动后台前阻止登录并提示')
        items[:] = [wired]
        window.scan()
        until(lambda: window.items == items)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blocker:
            blocker.bind(('127.0.0.1', port))
            attempt('conflict')
            until(lambda: window.state == 'error')
            assert '已有认证客户端' in window.message.get_text()
        passed('已有客户端占用端口时拒绝接管')

        attempt('logout-hang')
        until(lambda: window.state == 'online')
        window.action.emit('clicked')
        until(lambda: window.state == 'error', timeout=12)
        assert '服务器未确认退出' in window.message.get_text()
        passed('退出确认超时会停止本地进程，并说明服务器未确认')

        attempt('end-to-end')
        until(lambda: window.state == 'online')
        assert 'end-to-end' not in MemoryKeyring.values
        settings = (storage.CONFIG / 'settings.json').read_text()
        records = (storage.STATE / 'client.log').read_text()
        assert 'test-password' not in settings + records
        assert 'end-to-end' not in records
        assert (storage.CONFIG / 'settings.json').stat().st_mode & 0o777 == 0o600
        passed('成功后可继续重连；取消记住密码会清理密钥；配置与诊断不含密码')

        commands = [json.loads(line)['command'] for line in (engine / 'commands.jsonl').read_text().splitlines()]
        assert all(command in commands for command in ('GET', 'SET', 'LGI', 'LGO'))
        pid, old_window = window.client.process.pid, window
        application.request_quit()
        application.activate()
        until(lambda: application.window is not None and application.window is not old_window)
        window = application.window
        until(lambda: bool(window.items))
        assert not old_window.client.thread.is_alive() and not Path('/proc/%s' % pid).exists()
        assert window.state == 'idle' and window.get_visible()
        passed('退出清理期间重新激活，会在释放旧后台后恢复可用窗口')
        attempt('end-to-end')
        until(lambda: window.state == 'online')
        pid = window.client.process.pid
        menu_event(6)
        until(lambda: not window.client.thread.is_alive() and application.window is None, timeout=6)
        assert not Path('/proc/%s' % pid).exists()
        assert tray.closed
        until(lambda: not any(name.split('/')[0] == tray.name for name in call(
            'org.freedesktop.DBus.Properties', 'Get', '(ss)',
            (WATCHER, 'RegisteredStatusNotifierItems'), WATCHER_PATH, WATCHER)[0]))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as released:
            released.bind(('127.0.0.1', port))
        passed('托盘明确退出会注销图标、发出退出请求并清理线程、后台及端口')

        # A private mock watcher exercises disappearance/reappearance without touching GNOME settings.
        watcher_name = 'io.github.Anchirit.MyDrClient.Watcher' + uuid.uuid4().hex
        registrations = []
        watcher_xml = '<node><interface name="org.kde.StatusNotifierWatcher">' \
            '<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>' \
            '<property name="IsStatusNotifierHostRegistered" type="b" access="read"/></interface></node>'
        def register_item(connection, sender, path, interface, method, params, invocation):
            registrations.append(params.unpack()[0])
            invocation.return_value(None)
        watcher_export = bus.register_object(WATCHER_PATH, Gio.DBusNodeInfo.new_for_xml(watcher_xml).interfaces[0],
            register_item, lambda *args: GLib.Variant('b', True), None)
        fallback = Application(client_factory=lambda callback: Client(callback, directory=engine, port=port,
                network=lambda: items, privileged=False), network_loader=lambda: list(items),
            tray_factory=lambda *args: Tray(*args, watcher_name=watcher_name),
            application_id='io.github.Anchirit.MyDrClient.Test' + uuid.uuid4().hex)
        assert fallback.register(None)
        fallback.activate()
        until(lambda: bool(fallback.window.items))
        fallback.window.close()
        until(lambda: bool(fallback.window.get_surface().get_state() & Gdk.ToplevelState.MINIMIZED))
        assert fallback.window.get_visible() and not fallback.shutting_down
        fallback.activate()
        until(lambda: not fallback.window.get_surface().get_state() & Gdk.ToplevelState.MINIMIZED)
        owner = Gio.bus_own_name_on_connection(bus, watcher_name, Gio.BusNameOwnerFlags.NONE, None, None)
        until(lambda: fallback.tray.available)
        fallback.window.close()
        until(lambda: not fallback.window.get_visible())
        Gio.bus_unown_name(owner)
        until(lambda: not fallback.tray.available and fallback.window.get_visible())
        owner = Gio.bus_own_name_on_connection(bus, watcher_name, Gio.BusNameOwnerFlags.NONE, None, None)
        until(lambda: fallback.tray.available and len(registrations) >= 2)
        passed('无托盘时最小化可恢复；托盘宿主消失自动显示窗口，恢复后自动重新注册')
        fallback.background_action.change_state(GLib.Variant('b', False))
        assert storage.load()['background'] is False
        fallback.window.close()
        until(lambda: fallback.window is None)
        Gio.bus_unown_name(owner)
        bus.unregister_object(watcher_export)
        passed('关闭留在托盘选项可持久保存；关闭该选项后窗口关闭会完整退出')

        # Real engine uses its own network namespace; no test account reaches the campus.
        process = subprocess.run(['unshare', '--user', '--map-root-user', '--net', '--mount',
                                  '/usr/bin/python3', str(__file__), '--engine'],
                                 capture_output=True, text=True, timeout=25)
        assert process.returncode == 0, process.stdout + process.stderr
        print(process.stdout.strip(), flush=True)
        passed('独立网络命名空间中的真实后台端到端通信与清理')

        # Real keyring writes are opt-in; the normal suite only uses its in-memory fixture.
        keyring = real_keyring() if '--real-keyring' in sys.argv[1:] else None
        account = 'mydrclient-integration-' + uuid.uuid4().hex
        complete = []
        if keyring:
            keyring.store(account, 'integration-only-secret', lambda error: complete.append(error))
            until(lambda: bool(complete), timeout=8)
        if keyring is None:
            keyring_status = '本轮按项目范围限制未写入系统密钥环；凭据交互使用内存替身。'
        elif complete[0]:
            assert '锁定' in complete[0] or '不可用' in complete[0]
            keyring_status = '本机密钥环锁定/不可用，已验证明确提示；未验证解锁后的真实密码读写。'
            passed('真实密钥环锁定时及时反馈，不阻塞连接或静默保存密码')
        else:
            found = []
            keyring.lookup(account, lambda password, error: found.append((password, error)))
            until(lambda: bool(found))
            assert found[0] == ('integration-only-secret', '')
            removed = []
            keyring.clear(account, lambda error: removed.append(error or 'ok'))
            until(lambda: bool(removed))
            assert removed[0] == 'ok'
            keyring_status = '系统真实密钥环存储、读取、删除通过。'
            passed('系统真实密钥环完成测试密码存储、读取和删除')
        if keyring:
            keyring.cancel.cancel()

        manifest = json.loads((ROOT / 'vendor/SHA256SUMS.json').read_text())
        assert all(hashlib.sha256((ROOT / 'vendor' / name).read_bytes()).hexdigest() == digest
                   for name, digest in manifest.items())
        passed('发布包后台及资源校验一致')
        (output / 'report.json').write_text(json.dumps({'passed': results, 'keyring': keyring_status,
            'campus_authentication': '未发送真实校园网账号；需用户在新界面验证。'}, ensure_ascii=False, indent=2) + '\n')
        print('\n端到端验证完成：%d 项通过。真实校园网登录单独验证。' % len(results), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--engine']:
        isolated_engine()
    else:
        run()
