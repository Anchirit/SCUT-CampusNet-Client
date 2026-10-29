#!/usr/bin/python3
"""Exercise the test application's actual GNOME tray menu and capture only it."""
from collections import deque
from pathlib import Path
import sys
import time
import gi
gi.require_version('Atspi', '2.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Atspi, Gdk

Gdk.init([])
Atspi.set_timeout(1000, 2000)
output = Path(sys.argv[1])


def targets(names):
    desktop = Atspi.get_desktop(0)
    found = []
    for index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(index)
        if 'shell' not in app.get_name().lower():
            continue
        queue, count = deque([(app, 0)]), 0
        while queue and count < 5000:
            item, depth = queue.popleft()
            count += 1
            try:
                name = item.get_name()
                if name in names:
                    rect = item.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
                    if rect.x >= 0 and rect.y >= 0 and rect.width > 0:
                        found.append((name, rect))
                if depth < 18:
                    for child in range(min(item.get_child_count(), 100)):
                        queue.append((item.get_child_at_index(child), depth + 1))
            except Exception:
                continue
    return found


def save(rect, name):
    image = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(),
                                      rect.x, rect.y, rect.width, rect.height)
    assert image is not None
    image.savev(str(output / name), 'png', [], [])


deadline, icon = time.monotonic() + 8, None
while time.monotonic() < deadline and icon is None:
    icon = next((rect for name, rect in targets({'MyDrClient 校园网'}) if rect.y < 64), None)
    if icon is None:
        time.sleep(.05)
assert icon is not None, '桌面未显示测试应用的托盘按钮'
save(icon, 'tray-icon.png')
Atspi.generate_mouse_event(icon.x + icon.width // 2, icon.y + icon.height // 2, 'b1c')
required = {'显示窗口', '断开连接', '关闭时留在托盘', '退出程序'}
deadline, items = time.monotonic() + 5, []
while time.monotonic() < deadline:
    items = targets(required | {'MyDrClient · 待连接'})
    if required <= {name for name, rect in items}:
        break
    time.sleep(.05)
assert required <= {name for name, rect in items}, '桌面托盘菜单内容不完整'
rects = [rect for name, rect in items]
left, top = min(rect.x for rect in rects), min(rect.y for rect in rects)
right, bottom = max(rect.x + rect.width for rect in rects), max(rect.y + rect.height for rect in rects)
image = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), left, top, right - left, bottom - top)
assert image is not None
image.savev(str(output / 'tray-menu.png'), 'png', [], [])
rect = next(rect for name, rect in items if name == '显示窗口')
Atspi.generate_mouse_event(rect.x + rect.width // 2, rect.y + rect.height // 2, 'b1c')
print('实际桌面托盘图标与菜单可见，显示窗口菜单已点击。')
