#!/usr/bin/python3
"""Capture only the integration test's own X11 window, never the desktop."""
import sys
import gi
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gdk, GdkX11

Gdk.init([])
window = GdkX11.X11Window.foreign_new_for_display(Gdk.Display.get_default(), int(sys.argv[1]))
pixels = Gdk.pixbuf_get_from_window(window, 0, 0, window.get_width(), window.get_height())
if pixels is None:
    raise SystemExit('无法读取测试窗口。')
pixels.savev(sys.argv[2], 'png', [], [])
