#!/usr/bin/python3
"""GTK 4.6 frontend. All network and engine work runs outside the UI thread."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import time

os.environ.setdefault('GSK_RENDERER', 'cairo')
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from client import Client, adapters
import storage
from tray import Tray

ROOT = Path(__file__).resolve().parent


def label(text='', css=None):
    widget = Gtk.Label(label=text, xalign=0)
    if css:
        widget.add_css_class(css)
    return widget


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='MyDrClient · 校园网')
        self.app, self.state = app, 'idle'
        self.closing = False
        self.set_default_size(440, 740)
        self.set_size_request(400, 600)
        self.items, self.pending_password = [], ''
        self.connected_at = 0
        self.message_lines = []
        self.settings = storage.load()
        app.background_action.set_state(GLib.Variant('b', self.settings.get('background', True)))
        self.secret_accounts = {self.settings['account']} if self.settings.get('remember') and self.settings.get('account') else set()
        self.keyring = storage.Keyring()
        self.lookup_account = ''
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='network-scan')

        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT / 'style.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        header.set_title_widget(label('校园网', 'header-title'))
        menu = Gtk.MenuButton(icon_name='open-menu-symbolic', tooltip_text='应用菜单')
        menu.set_menu_model(app.menu)
        header.pack_end(menu)
        self.set_titlebar(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_child(scroll)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class('content')
        scroll.set_child(content)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_file(str(ROOT / 'mydrclient.svg'))
        icon.set_pixel_size(56)
        icon.add_css_class('brand-icon')
        hero.append(icon)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        heading.append(label('MyDrClient', 'title'))
        heading.append(label('让校园网连接更简单', 'muted'))
        hero.append(heading)
        content.append(hero)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dot = label('●', 'status-dot')
        self.status = label('待连接', 'status-title')
        self.elapsed = label('', 'muted')
        self.elapsed.set_hexpand(True)
        self.elapsed.set_halign(Gtk.Align.END)
        status_row.append(self.dot)
        status_row.append(self.status)
        status_row.append(self.elapsed)
        content.append(status_row)

        network = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        network.add_css_class('card')
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = label('连接网络', 'section-title')
        title.set_hexpand(True)
        row.append(title)
        self.refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic')
        self.refresh.add_css_class('flat')
        self.refresh.set_tooltip_text('刷新网卡')
        self.refresh.connect('clicked', lambda button: self.scan())
        row.append(self.refresh)
        network.append(row)
        self.selector = Gtk.DropDown.new_from_strings(['正在读取网卡…'])
        self.selector.connect('notify::selected', lambda widget, prop: self.network_details())
        network.append(self.selector)
        self.network_info = label('', 'network-info')
        self.network_info.set_selectable(True)
        self.network_info.set_wrap(True)
        network.append(self.network_info)
        content.append(network)

        credentials = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        credentials.append(label('校园网账号', 'field-label'))
        self.account = Gtk.Entry(placeholder_text='请输入校园网账号')
        self.account.set_text(self.settings.get('account', ''))
        self.account.set_max_length(64)
        self.account.connect('activate', lambda entry: self.password.grab_focus())
        self.account.connect('notify::has-focus', self.account_focus)
        credentials.append(self.account)
        credentials.append(label('密码', 'field-label'))
        self.password = Gtk.PasswordEntry(placeholder_text='请输入密码', show_peek_icon=True)
        self.password.connect('activate', lambda entry: self.action_clicked(None))
        credentials.append(self.password)
        self.remember = Gtk.CheckButton(label='将密码保存在系统密钥环')
        self.remember.set_active(bool(self.settings.get('remember', False)))
        credentials.append(self.remember)
        content.append(credentials)

        self.message = label('选择连接校园网的网卡，然后登录。', 'message')
        self.message.set_wrap(True)
        self.message.set_lines(3)
        self.message.set_selectable(True)
        content.append(self.message)
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spinner = Gtk.Spinner()
        action_row.append(self.spinner)
        self.action = Gtk.Button(label='连接校园网')
        self.action.add_css_class('suggested-action')
        self.action.add_css_class('connect-button')
        self.action.set_hexpand(True)
        self.action.connect('clicked', self.action_clicked)
        action_row.append(self.action)
        content.append(action_row)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        diagnostics = Gtk.Button(label='诊断记录')
        diagnostics.add_css_class('flat')
        diagnostics.connect('clicked', self.show_diagnostics)
        bottom.append(diagnostics)
        content.append(bottom)
        self.footer = label('', 'footer')
        self.footer.set_wrap(True)
        self.footer.set_justify(Gtk.Justification.CENTER)
        self.footer.set_halign(Gtk.Align.CENTER)
        content.append(self.footer)

        self.client = app.client_factory(lambda kind, message: GLib.idle_add(self.event, kind, message))
        self.connect('close-request', self.close_requested)
        self.scan()
        if self.account.get_text() and self.remember.get_active():
            self.lookup_password()
        self.tick = GLib.timeout_add_seconds(1, self.update_elapsed)

    def scan(self):
        self.refresh.set_sensitive(False)
        future = self.executor.submit(self.app.network_loader)
        def complete(result):
            try:
                items, error = result.result(), ''
            except Exception:
                items, error = [], '无法读取网卡信息，请检查系统网络服务。'
            GLib.idle_add(self.scan_complete, items, error)
        future.add_done_callback(complete)

    def scan_complete(self, items, error):
        if self.closing:
            return False
        self.refresh.set_sensitive(self.state not in ('starting', 'authenticating', 'disconnecting', 'online'))
        previous = self.selected().name if self.selected() else self.settings.get('interface', '')
        self.items = items
        self.selector.set_model(Gtk.StringList.new([item.name + ' · ' + item.kind for item in items]
                                                   or ['未发现物理网卡']))
        selected = next((index for index, item in enumerate(items)
                         if item.name == previous and not item.issue()), 0)
        self.selector.set_selected(selected)
        self.selector.set_sensitive(bool(items) and self.state in ('idle', 'error'))
        self.action.set_sensitive(bool(items))
        self.network_details()
        if error:
            self.event('message', error)
        return False

    def selected(self):
        index = self.selector.get_selected()
        return self.items[index] if index < len(self.items) else None

    def network_details(self):
        item = self.selected()
        if item:
            connection = '链路已连接' if item.carrier else '链路未连接'
            self.network_info.set_text(f'{connection}  ·  IPv4 {item.ipv4 or "未配置"}\nMAC  {item.mac.upper()}')
        else:
            self.network_info.set_text('请检查网卡驱动或网络连接，然后刷新。')

    def account_focus(self, entry, prop):
        if not entry.has_focus() and self.remember.get_active():
            self.lookup_password()

    def lookup_password(self):
        account = self.account.get_text().strip()
        if not account or account == self.lookup_account or self.password.get_text():
            return
        self.lookup_account = account
        def complete(password, error):
            if self.closing:
                return
            if account == self.account.get_text().strip() and not self.password.get_text():
                self.password.set_text(password)
            if error:
                self.event('message', error)
        self.keyring.lookup(account, complete)

    def action_clicked(self, button):
        if self.state in ('starting', 'authenticating'):
            self.client.cancel()
            return
        if self.state == 'online':
            self.client.disconnect()
            return
        if self.state == 'disconnecting':
            return
        item = self.selected()
        if item is None:
            self.event('message', '未发现可用网卡，请刷新网卡列表。')
            return
        account, password = self.account.get_text().strip(), self.password.get_text()
        self.pending_password = password
        self.settings = {'account': account, 'interface': item.name, 'remember': self.remember.get_active(),
                         'background': self.app.background_action.get_state().unpack()}
        try:
            storage.save(**self.settings)
        except OSError:
            self.event('message', '无法保存账号设置，请检查用户目录权限。')
        self.event('state', 'starting')
        self.client.login(item.name, account, password)

    def event(self, kind, message):
        if self.closing:
            return False
        try:
            storage.record(kind, message)
        except OSError:
            pass
        if kind == 'message':
            self.message.set_text(message)
            self.message_lines.append(message)
            self.message_lines = self.message_lines[-50:]
            return False
        if kind != 'state':
            return False
        self.state = message
        titles = {'idle': '待连接', 'starting': '检查连接', 'authenticating': '正在认证',
                  'online': '已连接', 'disconnecting': '正在断开', 'error': '连接未完成'}
        self.status.set_text(titles[message])
        busy = message in ('starting', 'authenticating', 'disconnecting')
        self.spinner.set_spinning(busy)
        self.action.set_label('取消连接' if message in ('starting', 'authenticating') else
                              '断开连接' if message == 'online' else
                              '正在断开…' if message == 'disconnecting' else '连接校园网')
        self.action.set_sensitive(message != 'disconnecting' and bool(self.items))
        for widget in (self.account, self.password, self.remember, self.selector, self.refresh):
            widget.set_sensitive(message in ('idle', 'error'))
        for name in ('connected', 'error', 'busy'):
            self.dot.remove_css_class(name)
        self.dot.add_css_class('connected' if message == 'online' else 'error' if message == 'error' else 'busy' if busy else 'status-dot')
        if message == 'online':
            self.connected_at = time.monotonic()
            account = self.settings['account']
            remember = self.settings['remember']
            def stored(error):
                if error:
                    self.event('message', error)
                elif remember:
                    self.secret_accounts.add(account)
            if self.settings['remember']:
                self.keyring.store(account, self.pending_password, stored)
            elif account in self.secret_accounts:
                self.keyring.clear(account, stored)
                self.secret_accounts.discard(account)
            self.pending_password = ''
        elif message in ('idle', 'error'):
            self.pending_password = ''
            self.connected_at = 0
            self.elapsed.set_text('')
        self.app.sync_ui()
        return False

    def update_elapsed(self):
        if self.closing:
            return False
        if self.connected_at:
            seconds = int(time.monotonic() - self.connected_at)
            self.elapsed.set_text(f'{seconds // 60:02d}:{seconds % 60:02d}')
        return True

    def show_diagnostics(self, button):
        dialog = Gtk.Window(application=self.app, title='连接诊断', transient_for=self, modal=True)
        dialog.set_default_size(460, 320)
        scroll = Gtk.ScrolledWindow()
        dialog.set_child(scroll)
        text = label('\n\n'.join(self.message_lines) or '暂无诊断记录。')
        text.set_wrap(True)
        text.set_selectable(True)
        for method in ('set_margin_top', 'set_margin_bottom', 'set_margin_start', 'set_margin_end'):
            getattr(text, method)(20)
        scroll.set_child(text)
        dialog.present()

    def close_requested(self, window):
        if self.app.shutting_down:
            return True
        if self.app.background_action.get_state().unpack():
            self.close_dialogs()
            if self.app.tray and self.app.tray.available:
                self.set_visible(False)
            else:
                self.minimize()
        else:
            self.app.request_quit()
        return True

    def close_dialogs(self):
        for window in self.app.get_windows():
            if window.get_transient_for() == self:
                window.destroy()

    def prepare_shutdown(self):
        self.closing = True
        self.close_dialogs()
        self.set_sensitive(False)
        self.status.set_text('正在退出')
        self.message.set_text('正在结束认证并释放后台…')
        self.spinner.start()
        GLib.source_remove(self.tick)
        self.keyring.cancel.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.client.close()


class Application(Gtk.Application):
    def __init__(self, *, client_factory=Client, network_loader=adapters, application_id=None,
                 tray_factory=Tray):
        super().__init__(application_id=application_id or 'io.github.Anchirit.MyDrClient',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.client_factory, self.network_loader = client_factory, network_loader
        self.tray_factory = tray_factory
        self.window, self.shutting_down = None, False
        self.tray, self.held, self.reopen_requested = None, False, False
        self.menu = Gio.Menu()

    def do_startup(self):
        if GLib.get_prgname() != 'mydrclient':
            GLib.set_prgname('mydrclient')
        if GLib.get_application_name() != 'MyDrClient 校园网':
            GLib.set_application_name('MyDrClient 校园网')
        Gtk.Application.do_startup(self)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(ROOT / 'icons'))
        Gtk.Window.set_default_icon_name('mydrclient')
        for name, callback in (('show', self.activate), ('disconnect', self.disconnect), ('quit', self.request_quit)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda action, parameter, callback=callback: callback())
            self.add_action(action)
        self.background_action = Gio.SimpleAction.new_stateful('background', None, GLib.Variant('b', True))
        self.background_action.connect('change-state', self.change_background)
        self.add_action(self.background_action)
        self.set_accels_for_action('app.quit', ['<Primary>q'])

    def change_background(self, action, value):
        action.set_state(value)
        if self.window and not self.shutting_down:
            self.window.settings['background'] = value.unpack()
            try:
                storage.save(**self.window.settings)
            except OSError:
                self.window.event('message', '无法保存关闭行为，本次选择仍然有效。')
            self.sync_ui()

    def sync_ui(self):
        if self.window is None:
            return
        state, background = self.window.state, self.background_action.get_state().unpack()
        cancellable = state in ('starting', 'authenticating')
        self.lookup_action('disconnect').set_enabled(not self.shutting_down and (cancellable or state == 'online'))
        self.lookup_action('quit').set_enabled(not self.shutting_down)
        self.background_action.set_enabled(not self.shutting_down)
        self.menu.remove_all()
        self.menu.append('显示窗口', 'app.show')
        self.menu.append('取消连接' if cancellable else '断开连接', 'app.disconnect')
        section = Gio.Menu()
        section.append('关闭时留在托盘', 'app.background')
        self.menu.append_section(None, section)
        section = Gio.Menu()
        section.append('退出程序', 'app.quit')
        self.menu.append_section(None, section)
        if not background:
            footer = '关闭窗口将断开连接并退出。'
        elif self.tray and self.tray.available:
            footer = '关闭窗口后留在托盘，连接继续保持。'
        else:
            footer = '当前桌面无可用托盘，关闭时最小化；可从任务栏恢复。'
        self.window.footer.set_text(footer)
        if self.tray:
            self.tray.update(state, self.window.status.get_text(), background, self.shutting_down)

    def tray_available(self, available):
        if not available and self.window and not self.window.get_visible() and not self.shutting_down:
            self.window.present()
        self.sync_ui()

    def disconnect(self):
        if self.window and not self.shutting_down:
            if self.window.state in ('starting', 'authenticating'):
                self.window.client.cancel()
            elif self.window.state == 'online':
                self.window.client.disconnect()

    def request_quit(self):
        if self.shutting_down or self.window is None:
            return
        self.shutting_down = True
        self.window.prepare_shutdown()
        self.sync_ui()
        GLib.timeout_add(50, self.finish_quit)

    def finish_quit(self):
        if self.window.client.thread.is_alive():
            return True
        self.window.destroy()
        self.window = None
        if self.reopen_requested:
            self.reopen_requested, self.shutting_down = False, False
            self.activate()
        else:
            if self.tray:
                self.tray.close()
                self.tray = None
            if self.held:
                self.release()
                self.held = False
            self.quit()
        return False

    def do_activate(self):
        if self.shutting_down:
            self.reopen_requested = True
            if self.window:
                self.window.present()
            return
        if not self.held:
            self.hold()
            self.held = True
        if self.window is None:
            self.window = Window(self)
        if self.tray is None and self.tray_factory and self.get_dbus_connection():
            try:
                self.tray = self.tray_factory(
                    {'show': self.activate, 'disconnect': self.disconnect, 'quit': self.request_quit,
                     'background': lambda: self.background_action.change_state(
                         GLib.Variant('b', not self.background_action.get_state().unpack()))}, self.tray_available)
            except GLib.Error:
                self.tray = None
        self.sync_ui()
        self.window.unminimize()
        self.window.present()


if __name__ == '__main__':
    sys.exit(Application().run(sys.argv))
