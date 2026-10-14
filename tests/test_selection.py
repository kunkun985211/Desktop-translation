"""无显示服务器即可运行的选择事件顺序与过期响应测试。"""

import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from selection import SelectionMonitor, SelectionState


class SelectionStateTests(unittest.TestCase):
    def test_old_primary_does_not_trigger_on_single_click(self):
        state = SelectionState()
        state.owner_changed(50)
        state.press(100)
        state.release(120)
        self.assertIsNone(state.request())

    def test_drag_update_requires_mouse_release(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(130)
        self.assertIsNone(state.request())
        state.release(170)
        self.assertIsNotNone(state.request())

    def test_primary_event_can_arrive_after_mouse_release(self):
        state = SelectionState()
        state.press(100)
        state.release(170)
        self.assertIsNone(state.request())
        state.owner_changed(180)
        self.assertIsNotNone(state.request())

    def test_primary_dispatch_before_record_dispatch_still_matches(self):
        state = SelectionState()
        state.owner_changed(140)
        state.press(100)
        state.release(160)
        self.assertIsNotNone(state.request())

    def test_keyboard_selection_long_after_release_does_not_trigger(self):
        state = SelectionState()
        state.press(100)
        state.release(150)
        state.owner_changed(1000)
        self.assertIsNone(state.request())

    def test_second_gesture_invalidates_pending_text(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(120)
        state.release(150)
        token = state.request()
        state.press(200)
        self.assertFalse(state.accepts(token))

    def test_new_primary_invalidates_pending_text(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(120)
        state.release(150)
        token = state.request()
        state.owner_changed(160)
        self.assertFalse(state.accepts(token))
        self.assertIsNotNone(state.request())

    def test_same_text_can_be_selected_again(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(110)
        state.release(130)
        first = state.request()
        state.press(200)
        state.owner_changed(210)
        state.release(230)
        second = state.request()
        self.assertNotEqual(first, second)
        self.assertTrue(state.accepts(second))

    def test_cancel_invalidates_pending_text(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(110)
        state.release(130)
        token = state.request()
        state.cancel()
        self.assertFalse(state.accepts(token))
        self.assertIsNone(state.request())

    def test_a_primary_change_is_requested_only_once(self):
        state = SelectionState()
        state.press(100)
        state.owner_changed(110)
        state.release(130)
        self.assertIsNotNone(state.request())
        self.assertIsNone(state.request())

    def test_x11_timestamp_wrap(self):
        state = SelectionState()
        state.press(0xFFFFFFF0)
        state.owner_changed(5)
        state.release(10)
        self.assertIsNotNone(state.request())

    def test_same_owner_double_click_does_not_require_new_owner_event(self):
        state = SelectionState()
        state.owner_changed(50)
        state.press(100, 20, 20)
        state.release(130, 20, 20)
        self.assertIsNone(state.request())
        state.press(180, 21, 20)
        state.release(210, 21, 20)
        self.assertIsNotNone(state.request())

    def test_same_owner_drag_does_not_require_new_owner_event(self):
        state = SelectionState()
        state.owner_changed(50)
        state.press(100, 20, 20)
        state.release(200, 100, 20)
        self.assertIsNotNone(state.request())

    def test_two_clicks_far_apart_do_not_count_as_double_click(self):
        state = SelectionState()
        state.owner_changed(50)
        state.press(100, 20, 20)
        state.release(130, 20, 20)
        state.press(180, 100, 100)
        state.release(210, 100, 100)
        self.assertIsNone(state.request())


class MonitorLifecycleTests(unittest.TestCase):
    def test_wayland_session_rejects_forced_x11_backend_before_connecting(self):
        """Xwayland 的 GdkX11Display 不能代表可以监听整个 Wayland 桌面。"""
        display_type = type("X11Display", (), {})
        gdk = MagicMock()
        gdk.Display.get_default.return_value = display_type()
        x11_connection = MagicMock(side_effect=AssertionError("不应建立 X11 监听连接"))
        modules = {
            "gi": MagicMock(),
            "gi.repository": SimpleNamespace(
                Gdk=gdk, GdkX11=SimpleNamespace(X11Display=display_type),
                GLib=MagicMock(), Gtk=MagicMock(),
            ),
            "Xlib": SimpleNamespace(X=MagicMock(), XK=MagicMock(),
                                     display=SimpleNamespace(Display=x11_connection)),
            "Xlib.ext": SimpleNamespace(record=MagicMock(), xfixes=MagicMock()),
        }
        monitor = SelectionMonitor(MagicMock(), MagicMock(), MagicMock(), lambda: False)
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "GDK_BACKEND": "x11"}), \
             patch.dict("sys.modules", modules):
            with self.assertRaisesRegex(RuntimeError, "Wayland"):
                monitor.start()
        x11_connection.assert_not_called()
        self.assertFalse(monitor._running)

    def test_record_startup_error_closes_record_connection_once(self):
        """监听线程已接管连接时，启动失败不能再次关闭同一连接。"""
        control = MagicMock()
        record_connection = MagicMock()
        record_connection.record_enable_context.side_effect = RuntimeError("record failed")
        display_type = type("X11Display", (), {})
        gdk = MagicMock()
        gdk.Display.get_default.return_value = display_type()
        modules = {
            "gi": MagicMock(),
            "gi.repository": SimpleNamespace(
                Gdk=gdk, GdkX11=SimpleNamespace(X11Display=display_type),
                GLib=MagicMock(), Gtk=MagicMock(),
            ),
            "Xlib": SimpleNamespace(
                X=SimpleNamespace(KeyPress=2, ButtonRelease=5),
                XK=SimpleNamespace(XK_Escape=0xFF1B),
                display=SimpleNamespace(Display=MagicMock(side_effect=[control, record_connection])),
            ),
            "Xlib.ext": SimpleNamespace(record=MagicMock(), xfixes=MagicMock()),
            "Xlib.protocol": SimpleNamespace(rq=MagicMock()),
        }
        monitor = SelectionMonitor(MagicMock(), MagicMock(), MagicMock(), lambda: False)
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}), \
             patch.dict("sys.modules", modules):
            with self.assertRaisesRegex(RuntimeError, "鼠标监听启动失败"):
                monitor.start()
        record_connection.close.assert_called_once()


class SelectionBoundsTests(unittest.TestCase):
    def setUp(self):
        self.selections = []
        self.monitor = SelectionMonitor(
            lambda text, x, y: self.selections.append(
                (text, x, y, self.monitor.selection_bounds)
            ),
            lambda *_: None, lambda: None, lambda: False,
        )
        self.monitor._running = True
        self.monitor._epoch = 1
        self.monitor._scale = 1
        self.monitor._X = SimpleNamespace(ButtonPress=4, ButtonRelease=5)
        self.monitor._GLib = SimpleNamespace(IO_IN=1)
        self.monitor._clipboard = MagicMock()
        self.monitor._primary_matches_focus = lambda: True
        self.monitor._drain_owner_events = lambda *_: True

    def request_selection(self, start, end):
        self.monitor._state.press(100, *start)
        self.monitor._state.owner_changed(150)
        self.monitor._state.release(200, *end)
        self.monitor._point = end
        self.monitor._request_text(1)
        return self.monitor._clipboard.request_text.call_args.args[0]

    def test_upward_drag_bounds_are_above_both_endpoints(self):
        received = self.request_selection((500, 420), (180, 120))
        self.assertIsNone(self.monitor.selection_bounds)
        received(None, "selected text", None)
        self.assertEqual(self.monitor.selection_bounds, (180, 96, 500, 444))
        self.assertEqual(self.selections, [("selected text", 180, 120, (180, 96, 500, 444))])

    def test_multiline_drag_bounds_include_both_lines_and_padding(self):
        received = self.request_selection((120, 100), (330, 220))
        received(None, "first line\nlast line", None)
        self.assertEqual(self.monitor.selection_bounds, (120, 76, 330, 244))

    def test_double_click_bounds_leave_space_above_word(self):
        self.monitor._state.press(20, 430, 205)
        self.monitor._state.release(40, 430, 205)
        received = self.request_selection((430, 205), (430, 205))
        received(None, "language", None)
        self.assertEqual(self.monitor.selection_bounds, (430, 181, 430, 229))

    def test_clicking_toolbar_does_not_replace_last_selection_bounds(self):
        received = self.request_selection((120, 100), (330, 220))
        received(None, "selection", None)
        self.monitor._handle_button(1, 4, 1, 300, 200, 55)
        self.assertEqual(self.monitor.selection_bounds, (120, 76, 330, 244))

    def test_late_text_does_not_replace_previous_selection_bounds(self):
        self.monitor.selection_bounds = (10, 20, 30, 40)
        received = self.request_selection((120, 100), (330, 220))
        self.monitor._state.press(300, 200, 55)
        received(None, "obsolete", None)
        self.assertEqual(self.monitor.selection_bounds, (10, 20, 30, 40))
        self.assertEqual(self.selections, [])

    def test_empty_text_does_not_commit_bounds(self):
        received = self.request_selection((120, 100), (330, 220))
        received(None, "  ", None)
        self.assertIsNone(self.monitor.selection_bounds)

    def test_stop_clears_selection_bounds(self):
        received = self.request_selection((120, 100), (330, 220))
        received(None, "selection", None)
        self.monitor.stop()
        self.assertIsNone(self.monitor.selection_bounds)


class SmokeWindowTests(unittest.TestCase):
    def test_confined_test_process_cannot_prevent_desktop_restore(self):
        from smoke_apps import terminate_created_process

        process = MagicMock()
        process.poll.return_value = None
        with patch("smoke_apps.os.killpg", side_effect=PermissionError("confined")):
            self.assertFalse(terminate_created_process(process))

    def test_destroyed_window_is_ignored_while_finding_new_test_window(self):
        from smoke_apps import window_title

        class WindowGone(Exception):
            pass

        window = MagicMock()
        window.get_full_property.side_effect = WindowGone()
        with patch.dict("sys.modules", {"Xlib.error": SimpleNamespace(XError=WindowGone)}):
            self.assertEqual(window_title(window, 1, 2), "")


if __name__ == "__main__":
    unittest.main()
