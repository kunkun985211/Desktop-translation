#!/usr/bin/env python3
"""划词助手：仅针对 Ubuntu GNOME X11 的桌面阅读工具。"""
import getpass
import signal
import sys
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, Gdk, Gio, GLib, AyatanaAppIndicator3

import config
from api_client import build_messages, stream_chat, APIError, ChatCancelled
from settings_ui import SettingsWindow
from selection import SelectionMonitor
from ui import SelectionBar, ResultWindow, apply_style, contains

MAX_SELECTION = 20000
MAX_CONVERSATION = 80000


class SettingsError(Exception):
    """启动配置读取失败；不把配置内容或凭据写入错误信息。"""


class Assistant(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=config.APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        try:
            self.settings = config.load_settings()
        except (OSError, ValueError, TypeError, AttributeError):
            raise SettingsError() from None
        self.api_key = ''
        self.request_id = 0
        self.cancel_event = None
        self.slots = threading.BoundedSemaphore(4)
        self.history = []
        self.text = ''
        self.mode = 'translate'
        self.point = (100, 100)
        self.bounds = None
        self.pending = None
        self.partial = ''
        self.render_id = 0
        self.failed_question = None
        self.failed_question_displayed = False
        self.answer_question = None
        self.answer_incomplete = False
        self.settings_window = None
        self.monitor = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        apply_style()
        self.bar = SelectionBar(self.choose)
        self.panel = ResultWindow(self.hide, self.send_question, self.switch_mode,
                                  self.stop_request, self.show_settings, self.retry)
        self.add_window(self.panel)
        self._setup_tray()
        try:
            self.api_key = config.load_key(self.settings)
        except GLib.Error:
            self._notify('系统密钥环暂时不可用，请打开设置重新配置。')
        try:
            self.monitor = SelectionMonitor(self.selected, self.clicked, self.hide, self.is_own_window)
            self.monitor.start()
        except Exception as error:
            self._notify(str(error) if isinstance(error, RuntimeError) else 'X11 划词监听启动失败。')
            self.settings['paused'] = True
            self.toggle.set_active(False)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if '--quit' in args:
            self.quit()
        elif '--show-last' in args:
            self.show_last()
        elif '--background' not in args or not self.api_key:
            self.show_settings()
        return 0

    def do_shutdown(self):
        self.stop_request()
        if self.monitor:
            self.monitor.stop()
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        Gtk.Application.do_shutdown(self)

    def _notify(self, text):
        notification = Gio.Notification.new('划词助手')
        notification.set_body(text)
        self.send_notification('status', notification)

    def _setup_tray(self):
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            config.APP_ID, 'accessories-dictionary-symbolic',
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_title('划词助手')
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        self.toggle = Gtk.CheckMenuItem(label='启用划词浮条')
        self.toggle.set_active(not self.settings['paused'])
        self.toggle.connect('toggled', self.toggle_monitor)
        menu.append(self.toggle)
        for text, callback in [('打开上次结果', self.show_last), ('设置', self.show_settings)]:
            item = Gtk.MenuItem(label=text)
            item.connect('activate', lambda _, action=callback: action())
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label='退出划词助手')
        item.connect('activate', lambda *_: self.quit())
        menu.append(item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def toggle_monitor(self, item):
        self.settings['paused'] = not item.get_active()
        if self.settings['paused']:
            self.hide()
        config.save_settings(self.settings)

    def is_own_window(self):
        return any(window.get_visible() and window.has_toplevel_focus()
                   for window in self.get_windows())

    def clicked(self, x, y):
        if any(contains(window, x, y) for window in [self.bar, *self.get_windows()]):
            return
        self.hide()

    def selected(self, text, x, y):
        if self.settings['paused']:
            return
        bounds = self.monitor.selection_bounds
        if bounds is None:
            return
        self.pending = (text, x, y, bounds)
        if not self.bar.show_at(x, y, bounds):
            self._notify('选区周围没有足够空间，请少选几行文字再试。')

    def choose(self, mode):
        if self.pending is None:
            return
        self.stop_request()
        self.text, x, y, self.bounds = self.pending
        self.point = (x, y)
        self.mode = mode
        self.history = []
        self.failed_question = None
        self.failed_question_displayed = False
        self.answer_question = None
        self.answer_incomplete = False
        self.bar.hide()
        if not self.panel.reset(self.text, mode, x, y, self.bounds):
            self._notify('选区周围没有足够空间显示结果，请少选几行文字再试。')
            return
        self.request()

    def switch_mode(self):
        self.pending = (self.text, *self.point, self.bounds)
        self.choose('explain' if self.mode == 'translate' else 'translate')

    def hide(self):
        self.stop_request()
        self.bar.hide()
        self.panel.hide()

    def stop_request(self):
        self.request_id += 1
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_event = None
        if self.render_id:
            GLib.source_remove(self.render_id)
            self.render_id = 0
        if hasattr(self, 'panel') and self.panel.busy:
            if self.partial:
                self.panel.update_answer(self.partial)
            self.panel.set_busy(False)
            self.panel.mark_answer_incomplete('已停止')
            self.panel.set_status('已停止；未完成的回答不会加入对话。')
            self.panel.retry.show()

    def send_question(self, question):
        if not self.history:
            self.panel.set_status('请先完成首次解释；失败或停止后可以点“重试”。', True)
            return False
        if len(question) > 8000:
            self.panel.set_status('问题过长，请缩短到 8000 字符以内。', True)
            return False
        if self.request(question):
            return True
        return False

    def retry(self):
        self.request(self.failed_question, retrying=True)

    def request(self, question=None, retrying=False):
        if not retrying:
            self.failed_question = question
            self.failed_question_displayed = False
        if len(self.text) > MAX_SELECTION:
            self.panel.set_status(f'选文超过 {MAX_SELECTION} 字符，请分段选取。', True)
            return False
        if not self.api_key:
            self.panel.set_status('请在设置中保存当前服务的 API Key，然后重试。', True)
            self.panel.retry.show()
            return False
        if sum(len(item['content']) for item in self.history) + len(self.text) + len(question or '') > MAX_CONVERSATION:
            self.panel.set_status('本次对话过长，请重新划词开始新对话。', True)
            return False
        if not self.slots.acquire(blocking=False):
            self.panel.set_status('正在结束之前的请求，请稍后重试。', True)
            self.panel.retry.show()
            return False
        self.stop_request()
        request_id = self.request_id
        cancel = threading.Event()
        self.cancel_event = cancel
        history = self.history + ([{'role': 'user', 'content': question}] if question else [])
        messages = build_messages(self.mode, self.text, history)
        if question and not self.failed_question_displayed:
            self.panel.add_question(question)
            self.failed_question_displayed = True
        replace = retrying and self.answer_incomplete and self.answer_question == question
        self.panel.begin_answer(replace=replace)
        self.answer_question = question
        self.answer_incomplete = True
        self.partial = ''
        key, connection = self.api_key, dict(self.settings)
        self.panel.set_status('正在请求 ' + config.provider_label(connection['provider']) + '…')

        def run():
            try:
                answer = stream_chat(key, messages, cancel,
                    lambda part: GLib.idle_add(self.delta, request_id, part),
                    model=connection['model'], provider=connection['provider'],
                    base_url=connection['base_url'])
                GLib.idle_add(self.completed, request_id, answer, history)
            except ChatCancelled:
                pass
            except APIError as error:
                GLib.idle_add(self.failed, request_id, str(error))
            except Exception:
                GLib.idle_add(self.failed, request_id, '请求处理发生异常，请重试。')
            finally:
                self.slots.release()

        threading.Thread(target=run, name='api-request', daemon=True).start()
        return True

    def delta(self, request_id, part):
        if request_id == self.request_id and self.panel.get_visible():
            self.partial += part
            self.panel.set_status('正在生成…')
            if not self.render_id:
                self.render_id = GLib.timeout_add(60, self.render, request_id)
        return False

    def render(self, request_id):
        self.render_id = 0
        if request_id == self.request_id and self.panel.get_visible():
            self.panel.update_answer(self.partial)
        return False

    def completed(self, request_id, answer, history):
        if request_id != self.request_id:
            return False
        if self.render_id:
            GLib.source_remove(self.render_id)
            self.render_id = 0
        self.panel.update_answer(answer)
        self.history = history + [{'role': 'assistant', 'content': answer}]
        self.failed_question = None
        self.failed_question_displayed = False
        self.answer_incomplete = False
        self.cancel_event = None
        self.panel.set_busy(False)
        self.panel.set_status(config.provider_label(self.settings['provider']) + ' · 点击窗口外部隐藏 · Esc 关闭')
        if self.mode == 'explain':
            self.panel.input.grab_focus()
        return False

    def failed(self, request_id, message):
        if request_id == self.request_id:
            self.request_id += 1
            if self.render_id:
                GLib.source_remove(self.render_id)
                self.render_id = 0
            if self.partial:
                self.panel.update_answer(self.partial)
            self.cancel_event = None
            self.panel.set_busy(False)
            self.panel.mark_answer_incomplete(message)
            self.panel.set_status(message, True)
            self.panel.retry.show()
        return False

    def show_last(self):
        self.bar.hide()
        if self.text:
            self.panel.show()
            self.panel.present()
        else:
            self._notify('在浏览器、PDF 阅读器或 IDE 中选中文字，即可看到“翻译 / 解释”。')

    def show_settings(self):
        self.bar.hide()
        if self.settings_window:
            self.settings_window.present()
            return
        window = SettingsWindow(self.settings, bool(self.api_key), self.save_connection)
        self.settings_window = window
        self.add_window(window)
        window.connect('destroy', lambda *_: setattr(self, 'settings_window', None))
        window.show_all()
        window.present()

    def save_connection(self, settings, new_key, autostart):
        # 暂停由托盘控制，设置窗口打开时的副本不能覆盖此后的切换。
        settings = config.normalize_settings(dict(settings, paused=self.settings['paused']))
        key = config.save_connection(settings, new_key, autostart)
        changed = (settings != self.settings or key != self.api_key)
        self.settings, self.api_key = settings, key
        if changed:
            # 连接配置变化后关闭旧会话，避免把已有对话发给另一服务。
            self.hide()
            self.history = []
            self.text = ''
            self.pending = None
            self.failed_question = None
            self.failed_question_displayed = False
            self.answer_question = None
            self.answer_incomplete = False
        self._notify('设置已保存；划词当前暂停，可从托盘启用。' if settings['paused']
                     else '已就绪：选中文字，点击“翻译”或“解释”。')


def main():
    if '--set-key' in sys.argv:
        value = getpass.getpass('输入当前服务的 API Key（不回显）：')
        try:
            config.store_key(value)
        except (ValueError, RuntimeError, GLib.Error):
            print('密钥保存失败，请检查格式或系统密钥环。', file=sys.stderr)
            return 1
        print('密钥已保存到系统密钥环。')
        return 0
    try:
        application = Assistant()
    except SettingsError:
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text='划词助手无法读取配置',
        )
        dialog.format_secondary_text(
            f'配置文件格式错误或无法读取：\n{config.CONFIG_FILE}\n\n'
            '请修正该文件或将它移走后重新启动。原文件未被修改。'
        )
        dialog.run()
        dialog.destroy()
        return 1
    return application.run(sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
