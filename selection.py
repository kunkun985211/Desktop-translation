"""GNOME X11 的 PRIMARY 划词监听；不修改剪贴板，也不发送网络请求。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
from typing import Callable


_LOG = logging.getLogger(__name__)


def _elapsed(later: int, earlier: int) -> int:
    """X11 时间戳为每约 49 天回绕一次的 uint32 毫秒。"""
    return (later - earlier) & 0xFFFFFFFF


@dataclass(frozen=True)
class SelectionToken:
    gesture: int
    owner_change: int


class SelectionState:
    """纯状态机：PRIMARY 更新或明确划词手势必须属于刚完成的操作。"""

    OWNER_GRACE_MS = 450

    def __init__(self, double_click_ms: int = 400, double_click_distance: int = 5) -> None:
        self.generation = 0
        self.owner_change = 0
        self.press_time: int | None = None
        self.release_time: int | None = None
        self.owner_time: int | None = None
        self.last_requested: SelectionToken | None = None
        self.double_click_ms = double_click_ms
        self.double_click_distance = double_click_distance
        self.press_point = (0, 0)
        self.selection_gesture = False

    def press(self, timestamp: int, x: int = 0, y: int = 0) -> None:
        self.selection_gesture = (
            self.press_time is not None
            and self.release_time is not None
            and _elapsed(timestamp, self.press_time) <= self.double_click_ms
            and max(abs(x - self.press_point[0]), abs(y - self.press_point[1]))
            <= self.double_click_distance
        )
        self.generation += 1
        self.press_time = timestamp
        self.release_time = None
        self.press_point = (x, y)

    def release(self, timestamp: int, x: int = 0, y: int = 0) -> None:
        if self.press_time is not None:
            self.release_time = timestamp
            self.selection_gesture |= max(
                abs(x - self.press_point[0]), abs(y - self.press_point[1])
            ) >= 4

    def owner_changed(self, timestamp: int) -> None:
        self.owner_change += 1
        self.owner_time = timestamp

    def cancel(self) -> None:
        self.generation += 1
        self.press_time = None
        self.release_time = None
        self.selection_gesture = False

    def candidate(self) -> SelectionToken | None:
        if self.press_time is None or self.release_time is None:
            return None
        gesture_length = _elapsed(self.release_time, self.press_time)
        if gesture_length >= 0x80000000:
            return None
        if self.owner_time is not None:
            owner_delay = _elapsed(self.owner_time, self.press_time)
            if owner_delay <= gesture_length + self.OWNER_GRACE_MS:
                return SelectionToken(self.generation, self.owner_change)
            if owner_delay < 0x80000000:
                return None
        if not self.selection_gesture:
            return None
        return SelectionToken(self.generation, self.owner_change)

    def request(self) -> SelectionToken | None:
        token = self.candidate()
        if token is None or token == self.last_requested:
            return None
        self.last_requested = token
        return token

    def accepts(self, token: SelectionToken) -> bool:
        return token == self.candidate()


class SelectionMonitor:
    """所有公开方法和四个回调都在 GTK 主线程执行。

    on_selection(text, x, y) 和 on_click(x, y) 使用 GDK 逻辑坐标。
    selection_bounds 是最后一次成功选文的 (left, top, right, bottom)，
    使用手势端点加上下各 24 像素估计，并非应用提供的精确文字矩形。
    它在 on_selection 回调前更新，普通点击不改写；启动/停止时清空。
    is_own_window() 应返回助手任一顶层窗口是否拥有键盘焦点。
    on_click 只报告按钮 1/2/3 的按下；调用者据窗口矩形判断内部点击。
    start() 在 X11 扩展不可用时抛 RuntimeError；stop() 可重复调用。
    """

    def __init__(
        self,
        on_selection: Callable[[str, int, int], None],
        on_click: Callable[[int, int], None],
        on_escape: Callable[[], None],
        is_own_window: Callable[[], bool],
    ) -> None:
        self.on_selection = on_selection
        self.on_click = on_click
        self.on_escape = on_escape
        self.is_own_window = is_own_window
        self._state = SelectionState()
        self._running = False
        self._epoch = 0
        self._watch_id = 0
        self._read_id = 0
        self._control = None
        self._record_context = None
        self._thread = None
        self._point = (0, 0)
        self.selection_bounds: tuple[int, int, int, int] | None = None

    def start(self) -> None:
        if self._running:
            return
        self.selection_bounds = None
        if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
            raise RuntimeError(
                "当前是 Wayland 桌面会话，Xwayland 无法提供全桌面划词。"
                "请退出登录，并在登录界面选择 Ubuntu on Xorg。"
            )
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkX11", "3.0")
        from gi.repository import Gdk, GdkX11, GLib, Gtk
        from Xlib import X, XK, display
        from Xlib.ext import record, xfixes

        gdk_display = Gdk.Display.get_default()
        if not isinstance(gdk_display, GdkX11.X11Display):
            raise RuntimeError("划词助手需要 GNOME X11 会话，请在登录界面选择 Ubuntu on Xorg。")
        self._GLib = GLib
        self._X = X
        self._record = record
        self._xfixes = xfixes
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        self._scale = Gdk.get_default_root_window().get_scale_factor()
        settings = Gtk.Settings.get_default()
        self._state = SelectionState(
            settings.get_property("gtk-double-click-time"),
            settings.get_property("gtk-double-click-distance"),
        )
        self._epoch += 1
        epoch = self._epoch
        record_display = None
        record_thread_started = False
        try:
            self._control = display.Display()
            if not self._control.has_extension("XFIXES"):
                raise RuntimeError("当前 X11 服务未提供 XFIXES 扩展，无法监听所选文字。")
            if not self._control.has_extension("X-Resource"):
                raise RuntimeError("当前 X11 服务未提供 X-Resource 扩展，无法确认所选文字来源。")
            self._control.xfixes_query_version()
            self._primary = self._control.intern_atom("PRIMARY")
            self._control.xfixes_select_selection_input(
                self._control.screen().root,
                self._primary,
                xfixes.XFixesSetSelectionOwnerNotifyMask
                | xfixes.XFixesSelectionWindowDestroyNotifyMask
                | xfixes.XFixesSelectionClientCloseNotifyMask,
            )
            self._control.flush()
            record_display = display.Display()
            if not record_display.has_extension("RECORD"):
                raise RuntimeError("当前 X11 服务未提供 RECORD 扩展，无法监听鼠标划词。")
            self._escape_keycode = record_display.keysym_to_keycode(XK.XK_Escape)
            self._record_context = record_display.record_create_context(
                0,
                [record.AllClients],
                [{
                    "core_requests": (0, 0),
                    "core_replies": (0, 0),
                    "ext_requests": (0, 0, 0, 0),
                    "ext_replies": (0, 0, 0, 0),
                    "delivered_events": (0, 0),
                    "device_events": (X.KeyPress, X.ButtonRelease),
                    "errors": (0, 0),
                    "client_started": False,
                    "client_died": False,
                }],
            )
            ready = threading.Event()
            startup_errors: list[Exception] = []
            self._running = True
            self._thread = threading.Thread(
                target=self._record_events,
                args=(record_display, self._record_context, epoch, ready, startup_errors),
                name="selection-xrecord",
                daemon=True,
            )
            self._thread.start()
            record_thread_started = True
            if not ready.wait(2) or startup_errors:
                raise RuntimeError("X11 全局鼠标监听启动失败。") from (
                    startup_errors[0] if startup_errors else None
                )
            self._watch_id = GLib.io_add_watch(
                self._control.fileno(),
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._drain_owner_events,
            )
        except Exception:
            self.stop()
            if record_display is not None and not record_thread_started:
                record_display.close()
            raise

    def stop(self) -> None:
        self._running = False
        self._epoch += 1
        self._state.cancel()
        self.selection_bounds = None
        for name in ("_watch_id", "_read_id"):
            source_id = getattr(self, name)
            if source_id:
                self._GLib.source_remove(source_id)
                setattr(self, name, 0)
        if self._control is not None:
            if self._record_context is not None:
                try:
                    self._control.record_disable_context(self._record_context)
                    self._control.flush()
                except Exception:
                    _LOG.debug("停止 XRecord 时连接已关闭", exc_info=True)
            if self._thread is not None:
                self._thread.join(timeout=0.5)
            self._control.close()
            self._control = None
        self._record_context = None
        self._thread = None

    def _record_events(self, connection, context, epoch, ready, startup_errors) -> None:
        from Xlib.protocol import rq

        def receive(reply) -> None:
            if reply.category == self._record.StartOfData:
                ready.set()
                return
            if reply.category != self._record.FromServer or not reply.data:
                return
            data = reply.data
            while data:
                event, data = rq.EventField(None).parse_binary_value(
                    data, connection.display, None, None
                )
                if event.type == self._X.KeyPress:
                    if event.detail == self._escape_keycode:
                        self._GLib.idle_add(self._handle_escape, epoch)
                elif event.type in (self._X.ButtonPress, self._X.ButtonRelease):
                    if event.detail in (1, 2, 3):
                        self._GLib.idle_add(
                            self._handle_button,
                            epoch, event.type, event.detail, event.time,
                            event.root_x, event.root_y,
                        )

        try:
            connection.record_enable_context(context, receive)
        except Exception as error:
            if not ready.is_set():
                startup_errors.append(error)
                ready.set()
            elif self._running and self._epoch == epoch:
                _LOG.exception("X11 全局鼠标监听连接中断")
        finally:
            try:
                connection.record_free_context(context)
                connection.close()
            except Exception:
                _LOG.debug("清理 XRecord 连接时连接已关闭", exc_info=True)

    def _handle_button(self, epoch, kind, button, timestamp, raw_x, raw_y) -> bool:
        if not self._running or epoch != self._epoch:
            return False
        x, y = round(raw_x / self._scale), round(raw_y / self._scale)
        if kind == self._X.ButtonPress:
            if button == 1:
                self._state.press(timestamp, x, y)
            else:
                self._state.cancel()
            self.on_click(x, y)
        elif button == 1:
            self._point = (x, y)
            self._state.release(timestamp, x, y)
            self._schedule_read()
        return False

    def _handle_escape(self, epoch) -> bool:
        if self._running and epoch == self._epoch:
            self._state.cancel()
            self.on_escape()
        return False

    def _drain_owner_events(self, _fd, condition) -> bool:
        if condition & (self._GLib.IO_HUP | self._GLib.IO_ERR):
            self._watch_id = 0
            self.stop()
            _LOG.error("X11 连接已关闭，划词监听已停止")
            return False
        while self._running and self._control.pending_events():
            event = self._control.next_event()
            if isinstance(event, self._xfixes.SelectionNotify) and event.selection == self._primary:
                self._state.owner_changed(event.timestamp)
                self._schedule_read()
        return self._running

    def _schedule_read(self) -> None:
        if self._state.candidate() is None:
            return
        if self._read_id:
            self._GLib.source_remove(self._read_id)
        self._read_id = self._GLib.timeout_add(100, self._request_text, self._epoch)

    def _request_text(self, epoch) -> bool:
        self._read_id = 0
        if not self._running or epoch != self._epoch:
            return False
        if self.is_own_window() or not self._primary_matches_focus():
            return False
        # 同步 X11 查询可能将通知读进 python-xlib 的缓冲区，先处理再取得 token。
        self._drain_owner_events(None, self._GLib.IO_IN)
        token = self._state.request()
        if token is None:
            return False
        point = self._point
        start = self._state.press_point
        bounds = (
            min(start[0], point[0]), min(start[1], point[1]) - 24,
            max(start[0], point[0]), max(start[1], point[1]) + 24,
        )

        def received(_clipboard, text, _data) -> None:
            if (
                self._running
                and epoch == self._epoch
                and self._state.accepts(token)
                and not self.is_own_window()
                and text
                and text.strip()
            ):
                self.selection_bounds = bounds
                self.on_selection(text.strip(), *point)

        self._clipboard.request_text(received, None)
        return False

    def _primary_matches_focus(self) -> bool:
        """PRIMARY 的拥有者必须属于当前焦点应用，排除别的应用的旧选文。"""
        from Xlib.error import XError

        try:
            owner = self._control.get_selection_owner(self._primary)
            focus = self._control.get_input_focus().focus
            if not hasattr(owner, "id") or not hasattr(focus, "id"):
                return False
            for client in self._control.res_query_clients().clients:
                mask = ~client.resource_mask
                if owner.id & mask == client.resource_base:
                    return focus.id & mask == client.resource_base
        except XError:
            # 鼠标释放后目标窗口可能立即销毁。
            return False
        return False
