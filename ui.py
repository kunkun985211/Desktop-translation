"""GTK3 原生划词浮条与阅读对话窗口。"""
import re

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

CSS = b'''
window.assistant { background: #f6f7fb; color: #283247; border: 1px solid #dce0eb; }
.assistant .heading { font-size: 16px; font-weight: 600; color: #293247; }
.assistant .brand-mark { color: #7560cd; background: #ede9fa; padding: 7px; border-radius: 9px; }
.assistant .muted { color: #727b8d; font-size: 12px; }
.assistant .accent { color: #7b7297; font-size: 12px; font-weight: 500; }
.assistant button { background: #ffffff; color: #505c72; border: 1px solid #dfe3ec;
    border-radius: 8px; padding: 6px 12px; box-shadow: none; text-shadow: none; }
.assistant button:hover { background: #eeedf7; border-color: #d4cdef; }
.assistant button:active { background: #e5e0f5; }
.assistant button.mode-switch { background: #ede9f9; color: #6b56ba;
    border-color: #e4def5; font-size: 12px; padding: 5px 11px; }
.assistant button.mode-switch:hover { background: #e3ddf6; border-color: #d4c9ef; }
.assistant button.icon-button { background: transparent; color: #82899b;
    border-color: transparent; min-width: 16px; min-height: 16px; padding: 6px; }
.assistant button.icon-button:hover { background: #e9e7f3; color: #65558f; }
.assistant button.close-button:hover { background: #f9e8eb; color: #ad5464; }
.assistant button.primary { background: #6b57ca; color: white; border-color: #6b57ca;
    font-weight: 600; padding: 6px 16px; }
.assistant button.primary:hover { background: #5e49bd; border-color: #5e49bd; }
.assistant button.primary:disabled { background: #e8e3f6; color: #958bad; border-color: #e8e3f6; }
.assistant .source { background: #eef0f6; color: #65718a; font-size: 13px;
    border-left: 2px solid #c9bfea; border-radius: 4px; padding: 8px 12px; }
.assistant .answer { background: #ffffff; border: 1px solid #e6e9f1;
    border-radius: 12px; padding: 13px; box-shadow: 0 1px 2px rgba(40, 50, 75, 0.03); }
.assistant textview, .assistant textview text { background: transparent; color: #2c374d; }
.assistant .question { background: #ede9f8; border: 1px solid #e4ddf4;
    border-radius: 10px; padding: 10px 12px; }
.assistant .composer-frame { background: #ffffff; border: 1px solid #dce1ed;
    border-radius: 10px; padding: 2px; }
.assistant .composer-frame.focused { border-color: #9a86dd;
    box-shadow: 0 0 0 2px rgba(137, 114, 202, 0.10); }
.assistant .compose-label { color: #665b84; font-size: 12px; font-weight: 600; }
.assistant spinner { color: #7962cf; }
.assistant .error { color: #b84452; }
window.selection-bar { background: #302c43; border: 1px solid #474057; border-radius: 11px; }
.selection-bar button { background: transparent; color: #f5f2fe; border: none;
    border-radius: 7px; padding: 8px 16px; font-size: 13px; font-weight: 500;
    box-shadow: none; text-shadow: none; }
.selection-bar button:hover { background: #514566; color: #ffffff; }
.selection-bar button:active { background: #64517e; }
'''


def apply_style():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def style(widget, name):
    widget.get_style_context().add_class(name)
    return widget


def label(text='', css=None):
    widget = Gtk.Label(label=text, xalign=0)
    if css:
        style(widget, css)
    return widget


def contains(window, x, y):
    if not window.get_visible():
        return False
    wx, wy = window.get_position()
    width, height = window.get_size()
    return wx <= x < wx + width and wy <= y < wy + height


def place_near(window, x, y, bounds):
    area = Gdk.Display.get_default().get_monitor_at_point(x, y).get_workarea()
    width, height = window.get_size()
    left, top, right, bottom = bounds
    gap, margin = 12, 8
    px = min(max((left + right - width) // 2, area.x + margin),
             area.x + area.width - width - margin)
    above = top - gap - (area.y + margin)
    below = area.y + area.height - margin - bottom - gap
    if height <= above:
        py = top - gap - height
    elif height <= below:
        py = bottom + gap
    else:
        # 长选区时先压缩可滚动窗口，仍保持与选区分离。
        minimum = window.get_preferred_height().minimum_size
        if max(above, below) >= minimum:
            height = max(above, below)
            window.resize(width, height)
            py = top - gap - height if above >= below else bottom + gap
        elif left - gap - width >= area.x + margin:
            px = left - gap - width
            py = min(max(top, area.y + margin), area.y + area.height - height - margin)
        elif right + gap + width <= area.x + area.width - margin:
            px = right + gap
            py = min(max(top, area.y + margin), area.y + area.height - height - margin)
        else:
            return False
    window.move(px, py)
    return True


class SelectionBar(Gtk.Window):
    def __init__(self, on_action):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_title('划词助手 · 翻译 / 解释')
        self.set_name('selection-assistant-bar')
        style(self, 'selection-bar')
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_border_width(4)
        self.anchor = None
        self.connect('size-allocate', self._allocated)
        box = Gtk.Box(spacing=2)
        self.add(box)
        for text, mode in [('翻译', 'translate'), ('解释', 'explain')]:
            button = Gtk.Button(label=text)
            button.connect('clicked', lambda _button, value=mode: on_action(value))
            box.pack_start(button, True, True, 0)

    def _allocated(self, *_):
        if self.anchor and not place_near(self, *self.anchor):
            self.hide()

    def show_at(self, x, y, bounds):
        self.anchor = (x, y, bounds)
        self.show_all()
        placed = place_near(self, x, y, bounds)
        if not placed:
            self.hide()
        return placed


class RichText(Gtk.TextView):
    """以文本标签渲染常用 Markdown；模型输出从不作为 HTML 执行。"""
    def __init__(self):
        super().__init__()
        self.set_editable(False)
        self.set_cursor_visible(False)
        self.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.set_pixels_above_lines(3)
        self.set_pixels_below_lines(3)
        self.set_left_margin(1)
        self.set_right_margin(1)
        self.buffer = self.get_buffer()
        self.buffer.create_tag('bold', weight=Pango.Weight.BOLD)
        self.buffer.create_tag('heading', weight=Pango.Weight.BOLD, scale=1.1)
        self.buffer.create_tag('code', family='monospace', background='#edf0f6')
        self._layout_size = None
        self._resize_pending = False
        self.connect('size-allocate', self._layout_changed)

    def _layout_changed(self, _view, allocation):
        size = (allocation.width, self.get_preferred_height()[1])
        if size != self._layout_size:
            self._layout_size = size
            if not self._resize_pending:
                self._resize_pending = True
                GLib.idle_add(self._resize_parent)

    def _resize_parent(self):
        # TextView 换行排版后，父容器须重新读取自然高度。
        self._resize_pending = False
        parent = self.get_parent()
        if parent is not None:
            parent.queue_resize()
        return False

    def render(self, text):
        self.buffer.set_text('')
        code = False
        for line in text.splitlines(keepends=True):
            if line.strip().startswith('```'):
                code = not code
                continue
            if code:
                self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), line, 'code')
                continue
            if re.match(r'^#{1,4} ', line):
                self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(),
                    re.sub(r'^#{1,4} ', '', line), 'heading')
                continue
            for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', line):
                if part.startswith('**') and part.endswith('**'):
                    self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), part[2:-2], 'bold')
                elif part.startswith('`') and part.endswith('`'):
                    self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), part[1:-1], 'code')
                else:
                    self.buffer.insert(self.buffer.get_end_iter(), part)


class ResultWindow(Gtk.Window):
    def __init__(self, on_hide, on_send, on_mode, on_stop, on_settings, on_retry):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title('划词助手')
        self.set_name('selection-assistant-result')
        style(self, 'assistant')
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_default_size(560, 440)
        self.set_border_width(18)
        self.on_send = on_send
        self.connect('delete-event', lambda *_: (on_hide(), True)[1])
        self.connect('key-press-event', self._key)
        self.on_hide = on_hide
        self.anchor = None
        self.connect('size-allocate', self._allocated)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)
        header = Gtk.Box(spacing=8)
        event = Gtk.EventBox()
        title_box = Gtk.Box(spacing=9)
        mark = style(Gtk.Image.new_from_icon_name('accessories-dictionary-symbolic', Gtk.IconSize.MENU), 'brand-mark')
        title_box.pack_start(mark, False, False, 0)
        heading = label('划词助手', 'heading')
        title_box.pack_start(heading, False, False, 0)
        event.add(title_box)
        event.connect('button-press-event', self._drag)
        header.pack_start(event, True, True, 0)
        self.mode_button = style(Gtk.Button(label='解释'), 'mode-switch')
        self.mode_button.connect('clicked', lambda *_: on_mode())
        header.pack_start(self.mode_button, False, False, 0)
        gear = style(Gtk.Button.new_from_icon_name('emblem-system-symbolic', Gtk.IconSize.MENU), 'icon-button')
        gear.set_tooltip_text('设置')
        gear.get_accessible().set_name('设置')
        gear.connect('clicked', lambda *_: on_settings())
        header.pack_start(gear, False, False, 0)
        close = style(Gtk.Button.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU), 'icon-button')
        style(close, 'close-button')
        close.set_tooltip_text('隐藏（Esc）')
        close.get_accessible().set_name('隐藏窗口')
        close.connect('clicked', lambda *_: on_hide())
        header.pack_start(close, False, False, 0)
        root.pack_start(header, False, False, 0)
        self.subtitle = label('', 'accent')
        root.pack_start(self.subtitle, False, False, 0)
        self.source = style(Gtk.Label(xalign=0), 'source')
        self.source.set_line_wrap(True)
        self.source.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.source.set_ellipsize(Pango.EllipsizeMode.END)
        self.source.set_lines(3)
        self.source.set_max_width_chars(64)
        root.pack_start(self.source, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_height(96)
        self._follow_bottom = True
        adjustment = self.scroll.get_vadjustment()
        adjustment.connect('changed', self._scroll_layout_changed)
        adjustment.connect('value-changed', self._scroll_position_changed)
        self.conversation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.scroll.add(self.conversation)
        root.pack_start(self.scroll, True, True, 0)
        self.footer = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        self.footer.pack_start(self.spinner, False, False, 0)
        self.status = label('', 'muted')
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(50)
        self.footer.pack_start(self.status, True, True, 0)
        self.stop = Gtk.Button(label='停止')
        self.stop.connect('clicked', lambda *_: on_stop())
        self.footer.pack_start(self.stop, False, False, 0)
        self.retry = Gtk.Button(label='重试')
        self.retry.connect('clicked', lambda *_: on_retry())
        self.footer.pack_start(self.retry, False, False, 0)
        root.pack_start(self.footer, False, False, 0)
        self.compose_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.input = Gtk.TextView()
        self.input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input.set_top_margin(7)
        self.input.set_bottom_margin(7)
        self.input.set_left_margin(8)
        self.input.set_right_margin(8)
        self.input.connect('key-press-event', self._input_key)
        input_scroll = style(Gtk.ScrolledWindow(), 'composer-frame')
        self.input.connect('focus-in-event', lambda *_: self._input_focus(input_scroll, True))
        self.input.connect('focus-out-event', lambda *_: self._input_focus(input_scroll, False))
        input_scroll.set_min_content_height(58)
        input_scroll.set_max_content_height(95)
        input_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        input_scroll.add(self.input)
        self.compose_box.pack_start(input_scroll, False, False, 0)
        bottom = Gtk.Box(spacing=8)
        bottom.pack_start(label('继续追问', 'compose-label'), False, False, 0)
        bottom.pack_start(label('Enter 发送 · Shift+Enter 换行', 'muted'), True, True, 0)
        self.send = style(Gtk.Button(label='发送'), 'primary')
        self.send.connect('clicked', lambda *_: self._send())
        bottom.pack_start(self.send, False, False, 0)
        self.compose_box.pack_start(bottom, False, False, 0)
        root.pack_start(self.compose_box, False, False, 0)
        self.answer = None
        self.answer_box = None
        self.answer_notice = None
        self.busy = False
        self.mode = 'translate'

    @staticmethod
    def _input_focus(frame, focused):
        context = frame.get_style_context()
        (context.add_class if focused else context.remove_class)('focused')
        return False

    def _drag(self, _widget, event):
        if event.button == 1:
            self.anchor = None
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)

    def _allocated(self, *_):
        if self.anchor and not place_near(self, *self.anchor):
            self.on_hide()

    def _key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.on_hide()
            return True
        return False

    def _input_key(self, _widget, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not event.state & Gdk.ModifierType.SHIFT_MASK:
            self._send()
            return True
        return False

    def _send(self):
        if self.busy:
            return
        buf = self.input.get_buffer()
        value = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        if value and self.on_send(value):
            buf.set_text('')

    def reset(self, text, mode, x, y, bounds):
        self.mode = mode
        self.source.set_text(text)
        self.subtitle.set_text('自动识别语言 → 简体中文' if mode == 'translate' else '中文解释 · VLA / 机器人学习 / 编程')
        self.mode_button.set_label('解释' if mode == 'translate' else '翻译')
        for child in self.conversation.get_children():
            self.conversation.remove(child)
        self.answer = None
        self.answer_box = None
        self.answer_notice = None
        self.input.get_buffer().set_text('')
        self.anchor = (x, y, bounds)
        self.show_all()
        self.compose_box.set_visible(mode == 'explain')
        self.stop.hide()
        self.retry.hide()
        self.resize(560, 340 if mode == 'translate' else 440)
        if not place_near(self, x, y, bounds):
            self.hide()
            return False
        self.present()
        return True

    def add_question(self, text):
        question = style(RichText(), 'question')
        question.get_buffer().set_text(text)
        self.conversation.pack_start(question, False, False, 0)
        question.show()

    def begin_answer(self, replace=False):
        self.retry.hide()
        if replace and self.answer_box is not None:
            self.conversation.remove(self.answer_box)
        self.answer_box = style(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), 'answer')
        self.answer = RichText()
        self.answer_box.pack_start(self.answer, False, False, 0)
        self.answer_notice = style(label('', 'muted'), 'error')
        self.answer_notice.set_line_wrap(True)
        self.answer_notice.set_max_width_chars(54)
        self.answer_box.pack_start(self.answer_notice, False, False, 0)
        self.conversation.pack_start(self.answer_box, False, False, 0)
        self.answer_box.show_all()
        self.answer_notice.hide()
        self.set_busy(True)
        self.set_status('正在请求 API…')

    def mark_answer_incomplete(self, message):
        if self.answer_notice is not None:
            self.answer_notice.set_text('未完成 · ' + message + '（未加入对话上下文）')
            self.answer_notice.show()

    def set_busy(self, busy):
        self.busy = busy
        self.send.set_sensitive(not busy)
        self.stop.set_visible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.spinner.set_visible(busy)

    def set_status(self, text, error=False):
        self.status.set_text(text)
        context = self.status.get_style_context()
        (context.add_class if error else context.remove_class)('error')

    def update_answer(self, text):
        adjustment = self.scroll.get_vadjustment()
        self._follow_bottom = self._near_bottom(adjustment)
        self.answer.render(text)

    @staticmethod
    def _near_bottom(adjustment):
        return adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 80

    def _scroll_position_changed(self, adjustment):
        self._follow_bottom = self._near_bottom(adjustment)

    def _scroll_layout_changed(self, adjustment):
        # 按实际布局后的范围跟随底部；向上阅读时保留当前位置。
        if self._follow_bottom:
            adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
