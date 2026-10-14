"""需要当前 X11 显示器的窗口回归测试；不调用网络。"""
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui
from ui import ResultWindow, apply_style
from gi.repository import Gtk, Gdk, GLib


def settle(milliseconds=100):
    loop = GLib.MainLoop()
    GLib.timeout_add(milliseconds, lambda: (loop.quit(), False)[1])
    loop.run()


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_style()

    def setUp(self):
        self.panel = ResultWindow(lambda: None, lambda _: True, lambda: None,
                                  lambda: None, lambda: None, lambda: None)

    def tearDown(self):
        self.panel.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def test_final_height_stays_inside_workarea(self):
        area = Gdk.Display.get_default().get_primary_monitor().get_workarea()
        # 最终分配尺寸可能与 resize 请求不同，不能越过工作区边界。
        y = area.y + area.height - 610 - 15
        self.panel.reset('action chunking', 'explain', area.x + area.width - 20, y,
                         (area.x + area.width - 200, y - 24, area.x + area.width - 20, y + 24))
        settle()
        x, y = self.panel.get_position()
        width, height = self.panel.get_size()
        self.assertLessEqual(x + width, area.x + area.width)
        self.assertLessEqual(y + height, area.y + area.height)

    def test_retry_replaces_only_the_incomplete_answer(self):
        self.panel.reset('action chunking', 'explain', 100, 100, (100, 76, 200, 124))
        self.panel.begin_answer()
        self.panel.update_answer('未接收完整')
        self.panel.mark_answer_incomplete('连接中断')
        self.panel.begin_answer(replace=True)
        self.panel.update_answer('完整回答')
        self.assertEqual(len(self.panel.conversation.get_children()), 1)
        self.assertFalse(self.panel.answer_notice.get_visible())

    def test_final_allocation_remains_above_word(self):
        self.panel.reset('action chunking', 'explain', 1000, 1100, (800, 1076, 1000, 1124))
        self.panel.begin_answer()
        self.panel.update_answer('一次预测一小段未来动作序列。')
        settle()
        _, y = self.panel.get_position()
        _, height = self.panel.get_size()
        self.assertLessEqual(y + height, 1076 - 12)

    def test_short_answer_starts_visible_after_layout(self):
        self.panel.reset('action chunking', 'explain', 1800, 1300,
                         (1600, 1276, 1800, 1324))
        self.panel.begin_answer()
        self.panel.update_answer(
            '**动作分块**是指一次预测一小段未来动作，而不是每一步都重新决策。'
            '例如模型一次输出未来 8 步关节指令，再由控制器逐步执行。'
            '这样可以减少推理次数，让动作更连贯。')
        self.panel.set_busy(False)
        self.panel.input.grab_focus()
        settle(400)
        adjustment = self.panel.scroll.get_vadjustment()
        natural_height = self.panel.answer_box.get_preferred_height()[1]
        self.assertLessEqual(natural_height, adjustment.get_page_size())
        self.assertEqual(adjustment.get_value(), 0,
                         '能完整放下的短答案不应向下滚动、裁掉开头')

    def test_long_answer_follows_bottom_after_layout(self):
        self.panel.reset('action chunking', 'explain', 1800, 1300,
                         (1600, 1276, 1800, 1324))
        self.panel.begin_answer()
        self.panel.update_answer('一次预测未来动作序列，让动作更连贯。\n' * 20)
        self.panel.set_busy(False)
        settle(400)
        adjustment = self.panel.scroll.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        self.assertAlmostEqual(adjustment.get_value(),
                               adjustment.get_upper() - adjustment.get_page_size(), delta=1)

    def test_streaming_preserves_reading_position_when_scrolled_up(self):
        self.panel.reset('action chunking', 'explain', 1800, 1300,
                         (1600, 1276, 1800, 1324))
        self.panel.begin_answer()
        text = '一次预测未来动作序列，让动作更连贯。\n' * 20
        self.panel.update_answer(text)
        settle(400)
        adjustment = self.panel.scroll.get_vadjustment()
        adjustment.set_value(30)
        self.panel.update_answer(text + '补充内容。\n' * 5)
        settle(400)
        self.assertAlmostEqual(adjustment.get_value(), 30, delta=1)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.window = MagicMock()
        self.window.get_size.return_value = (200, 60)
        self.area = type('Area', (), {'x': 0, 'y': 30, 'width': 1200, 'height': 900})()
        self.display = MagicMock()
        self.display.get_monitor_at_point.return_value.get_workarea.return_value = self.area

    def place(self, bounds):
        with patch('ui.Gdk.Display.get_default', return_value=self.display):
            ui.place_near(self.window, 450, 530, bounds)
        return self.window.move.call_args.args

    def test_toolbar_is_above_selection_with_gap(self):
        x, y = self.place((300, 460, 500, 540))
        self.assertLessEqual(y + 60, 448)

    def test_top_edge_uses_below_without_covering_selection(self):
        x, y = self.place((300, 40, 500, 85))
        self.assertGreaterEqual(y, 97)

    def test_upward_multiline_drag_uses_top_of_whole_selection(self):
        self.window.get_size.return_value = (560, 350)
        x, y = self.place((300, 600, 700, 1000))
        self.assertLessEqual(y + 350, 588)


if __name__ == '__main__':
    unittest.main()
