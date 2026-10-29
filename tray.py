"""GTK-independent StatusNotifierItem and DBusMenu, using the existing session bus."""
import os
from pathlib import Path
import uuid

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib

ROOT = Path(__file__).resolve().parent
ITEM = 'org.kde.StatusNotifierItem'
MENU = 'com.canonical.dbusmenu'
WATCHER = 'org.kde.StatusNotifierWatcher'
WATCHER_PATH = '/StatusNotifierWatcher'

ITEM_XML = '''<node><interface name="org.kde.StatusNotifierItem">
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="u" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionMovieName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg type="s"/></signal>
</interface></node>'''

MENU_XML = '''<node><interface name="com.canonical.dbusmenu">
  <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
  <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/></method>
  <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/></method>
  <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
  <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>'''


class Tray:
    def __init__(self, actions, available_changed, *, watcher_name=WATCHER):
        # Hosts may track the unique connection name rather than our owned service.
        # Closing a private connection removes the item immediately, even while
        # the GTK application is still finishing its shutdown.
        bus = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        bus.set_exit_on_close(False)
        self.bus, self.actions, self.available_changed = bus, actions, available_changed
        self.watcher_name, self.watcher_owner = watcher_name, ''
        self.name = f'org.kde.StatusNotifierItem-{os.getpid()}-{uuid.uuid4().int % 1000000000}'
        self.available, self.closed, self.owned = False, False, False
        self.state, self.title, self.background, self.quitting = 'idle', '待连接', True, False
        self.revision, self.pixmaps = 1, {}
        self.cancel = Gio.Cancellable()
        self.registrations = [
            bus.register_object('/StatusNotifierItem', Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0],
                                self._item_method, self._item_property, None),
            bus.register_object('/MenuBar', Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0],
                                self._menu_method, self._menu_property, None)]
        self.owner = Gio.bus_own_name_on_connection(bus, self.name, Gio.BusNameOwnerFlags.NONE,
                                                   self._name_acquired, self._name_lost)
        self.watch = Gio.bus_watch_name_on_connection(bus, watcher_name, Gio.BusNameWatcherFlags.NONE,
                                                      self._watcher_appeared, self._watcher_vanished)
        self.subscription = bus.signal_subscribe(watcher_name, 'org.freedesktop.DBus.Properties',
            'PropertiesChanged', WATCHER_PATH, WATCHER, Gio.DBusSignalFlags.NONE, self._host_changed)

    def _set_available(self, available):
        if self.available != available:
            self.available = available
            self.available_changed(available)

    def _name_acquired(self, connection, name):
        self.owned = True
        self._register()

    def _name_lost(self, connection, name):
        self.owned = False
        self._set_available(False)

    def _watcher_appeared(self, connection, name, owner):
        self.watcher_owner = owner
        self._register()

    def _watcher_vanished(self, connection, name):
        self.watcher_owner = ''
        self._set_available(False)

    def _register(self):
        if self.closed or not self.owned or not self.watcher_owner:
            return
        owner = self.watcher_owner
        def registered(connection, result):
            try:
                connection.call_finish(result)
            except GLib.Error:
                if not self.closed and self.watcher_owner == owner:
                    self._set_available(False)
                return
            if not self.closed and self.watcher_owner == owner:
                self._query_host()
        self.bus.call(owner, WATCHER_PATH, WATCHER, 'RegisterStatusNotifierItem',
                      GLib.Variant('(s)', (self.name,)), None, Gio.DBusCallFlags.NONE,
                      2000, self.cancel, registered)

    def _query_host(self):
        owner = self.watcher_owner
        def complete(connection, result):
            try:
                available = bool(connection.call_finish(result).unpack()[0])
            except GLib.Error:
                available = False
            if not self.closed and self.watcher_owner == owner:
                self._set_available(available)
        self.bus.call(owner, WATCHER_PATH, 'org.freedesktop.DBus.Properties', 'Get',
                      GLib.Variant('(ss)', (WATCHER, 'IsStatusNotifierHostRegistered')),
                      None, Gio.DBusCallFlags.NONE, 2000, self.cancel, complete)

    def _host_changed(self, connection, sender, path, interface, signal, parameters):
        if not self.closed and self.watcher_owner:
            self._query_host()

    def update(self, state, title, background, quitting=False):
        if self.closed:
            return
        self.state, self.title, self.background, self.quitting = state, title, background, quitting
        self.revision = (self.revision + 1) % (2 ** 32)
        for signal in ('NewIcon', 'NewToolTip'):
            self.bus.emit_signal(None, '/StatusNotifierItem', ITEM, signal, None)
        self.bus.emit_signal(None, '/MenuBar', MENU, 'LayoutUpdated',
                             GLib.Variant('(ui)', (self.revision, 0)))

    def _icon(self):
        kind = ('online' if self.state == 'online' else 'error' if self.state == 'error' else
                'busy' if self.quitting or self.state in ('starting', 'authenticating', 'disconnecting') else 'idle')
        return ROOT / 'icons' / f'mydrclient-tray-{kind}.svg'

    def _pixels(self):
        path = self._icon()
        if path not in self.pixmaps:
            images = []
            for size in (16, 22, 32):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
                pixels, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
                argb = bytearray()
                for y in range(size):
                    for x in range(size):
                        offset = y * stride + x * channels
                        argb.extend((pixels[offset + 3] if channels == 4 else 255,
                                     pixels[offset], pixels[offset + 1], pixels[offset + 2]))
                images.append((size, size, bytes(argb)))
            self.pixmaps[path] = images
        return self.pixmaps[path]

    def _item_property(self, connection, sender, path, interface, name):
        strings = {'Category': 'ApplicationStatus', 'Id': 'mydrclient', 'Title': 'MyDrClient 校园网',
                   'Status': 'Active', 'IconThemePath': str(ROOT / 'icons'), 'IconName': str(self._icon()),
                   'OverlayIconName': '', 'AttentionIconName': '', 'AttentionMovieName': ''}
        if name in strings:
            return GLib.Variant('s', strings[name])
        if name.endswith('Pixmap'):
            return GLib.Variant('a(iiay)', self._pixels() if name == 'IconPixmap' else [])
        if name == 'ToolTip':
            return GLib.Variant('(sa(iiay)ss)', (str(self._icon()), self._pixels(), 'MyDrClient 校园网', self.title))
        return {'WindowId': GLib.Variant('u', 0), 'ItemIsMenu': GLib.Variant('b', False),
                'Menu': GLib.Variant('o', '/MenuBar')}[name]

    def _item_method(self, connection, sender, path, interface, method, parameters, invocation):
        invocation.return_value(None)
        if method in ('Activate', 'SecondaryActivate', 'ContextMenu'):
            GLib.idle_add(self._activate, 'show')

    def _activate(self, action):
        if not self.closed:
            self.actions[action]()
        return False

    def _items(self):
        def properties(label, enabled=True, **extra):
            return {'label': GLib.Variant('s', label), 'enabled': GLib.Variant('b', enabled), **extra}
        cancellable = self.state in ('starting', 'authenticating')
        return {
            0: {'children-display': GLib.Variant('s', 'submenu')},
            1: properties('MyDrClient · ' + self.title, False),
            2: properties('显示窗口'),
            3: properties('取消连接' if cancellable else '断开连接',
                          not self.quitting and (cancellable or self.state == 'online')),
            4: properties('关闭时留在托盘', not self.quitting,
                          **{'toggle-type': GLib.Variant('s', 'checkmark'),
                             'toggle-state': GLib.Variant('i', int(self.background))}),
            5: {'type': GLib.Variant('s', 'separator')},
            6: properties('退出程序', not self.quitting)}

    def _menu_property(self, connection, sender, path, interface, name):
        return {'Version': GLib.Variant('u', 4), 'TextDirection': GLib.Variant('s', 'ltr'),
                'Status': GLib.Variant('s', 'normal'),
                'IconThemePath': GLib.Variant('as', [str(ROOT / 'icons')])}[name]

    def _event(self, item, event):
        properties = self._items().get(item)
        if properties is None:
            return False
        actions = {2: 'show', 3: 'disconnect', 4: 'background', 6: 'quit'}
        if event == 'clicked' and item in actions and properties.get('enabled', GLib.Variant('b', True)).unpack():
            GLib.idle_add(self._activate, actions[item])
        return True

    def _menu_method(self, connection, sender, path, interface, method, parameters, invocation):
        args, items = parameters.unpack(), self._items()
        def filtered(item, names):
            return {key: value for key, value in items[item].items() if not names or key in names}
        try:
            result = None
            if method == 'GetLayout':
                parent, depth, names = args
                children = [GLib.Variant('(ia{sv}av)', (item, filtered(item, names), []))
                            for item in range(1, 7)] if parent == 0 and depth != 0 else []
                result = GLib.Variant('(u(ia{sv}av))', (self.revision, (parent, filtered(parent, names), children)))
            elif method == 'GetGroupProperties':
                ids, names = args
                result = GLib.Variant('(a(ia{sv}))', ([(item, filtered(item, names))
                                                     for item in ids or items if item in items],))
            elif method == 'GetProperty':
                result = GLib.Variant('(v)', (items[args[0]][args[1]],))
            elif method == 'Event':
                self._event(args[0], args[1])
            elif method == 'EventGroup':
                result = GLib.Variant('(ai)', ([item for item, event, data, timestamp in args[0]
                                                if not self._event(item, event)],))
            elif method == 'AboutToShow':
                if args[0] not in items:
                    raise KeyError(args[0])
                result = GLib.Variant('(b)', (False,))
            elif method == 'AboutToShowGroup':
                result = GLib.Variant('(aiai)', ([], [item for item in args[0] if item not in items]))
            invocation.return_value(result)
        except (KeyError, ValueError):
            invocation.return_dbus_error('com.canonical.dbusmenu.InvalidMenuItem', 'Unknown menu item or property')

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel.cancel()
        Gio.bus_unwatch_name(self.watch)
        self.bus.signal_unsubscribe(self.subscription)
        Gio.bus_unown_name(self.owner)
        for registration in self.registrations:
            self.bus.unregister_object(registration)
        self._set_available(False)
        if not self.bus.is_closed():
            self.bus.close_sync(None)
