"""应用状态回归测试；替换线程、GUI 和凭据，不访问网络或密钥环。"""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from deepseek_client import build_messages


def make_assistant():
    panel = MagicMock()
    panel.busy = False
    panel.get_visible.return_value = True
    panel.set_busy.side_effect = lambda value: setattr(panel, "busy", value)
    panel.begin_answer.side_effect = lambda **kwargs: setattr(panel, "busy", True)
    state = SimpleNamespace(
        text="VLA", history=[{"role": "assistant", "content": "首次完整解释"}],
        api_key="fake-test-key", settings={"model": "deepseek-flash"},
        slots=threading.BoundedSemaphore(4), request_id=1, cancel_event=None,
        render_id=0, failed_question=None, failed_question_displayed=False,
        answer_question=None, answer_incomplete=False, mode="explain",
        panel=panel, bar=MagicMock(), partial="", pending=None,
    )
    for name in ("request", "stop_request", "retry", "completed", "failed", "delta", "hide"):
        setattr(state, name, getattr(app.Assistant, name).__get__(state))
    return state


class AppStateTests(unittest.TestCase):
    def setUp(self):
        self.state = make_assistant()
        self.threads = patch("app.threading.Thread").start()
        self.glib = patch("app.GLib").start()
        self.addCleanup(patch.stopall)

    def test_slot_exhaustion_retry_sends_latest_unshown_question(self):
        state = self.state
        for question in ("q1", "q2", "q3", "q4"):
            self.assertTrue(state.request(question))
            state.stop_request()
        self.assertFalse(state.request("q5"))
        state.panel.add_question.reset_mock()
        state.slots.release()
        with patch("app.build_messages", wraps=build_messages) as messages:
            state.retry()
        self.assertEqual(messages.call_args.args[2][-1]["content"], "q5")
        state.panel.add_question.assert_called_once_with("q5")
        state.panel.begin_answer.assert_called_with(replace=False)

    def test_failed_followup_retry_replaces_answer_without_duplicate_question(self):
        state = self.state
        original = list(state.history)
        state.request("举个例子")
        failed_id = state.request_id
        state.partial = "不完整内容"
        state.failed(failed_id, "连接中断")
        self.assertEqual(state.history, original)
        state.panel.mark_answer_incomplete.assert_called_once_with("连接中断")
        state.panel.add_question.reset_mock()
        with patch("app.build_messages", wraps=build_messages) as messages:
            state.retry()
        self.assertEqual(messages.call_args.args[2], original + [{"role": "user", "content": "举个例子"}])
        state.panel.add_question.assert_not_called()
        state.panel.begin_answer.assert_called_with(replace=True)

    def test_new_question_after_failure_does_not_commit_failed_exchange(self):
        state = self.state
        original = list(state.history)
        state.request("失败的问题")
        state.failed(state.request_id, "请求失败")
        with patch("app.build_messages", wraps=build_messages) as messages:
            state.request("换一个问题")
        self.assertEqual(messages.call_args.args[2], original + [{"role": "user", "content": "换一个问题"}])
        state.panel.begin_answer.assert_called_with(replace=False)

    def test_cancelled_request_cannot_change_new_conversation(self):
        state = self.state
        state.request("旧问题")
        old_id, old_cancel = state.request_id, state.cancel_event
        state.hide()
        self.assertTrue(old_cancel.is_set())
        state.text = "新选文"
        state.history = []
        state.request()
        current_id = state.request_id
        state.panel.reset_mock()
        state.delta(old_id, "迟到片段")
        state.completed(old_id, "迟到答案", [{"role": "user", "content": "旧问题"}])
        state.failed(old_id, "迟到错误")
        self.assertEqual(state.request_id, current_id)
        self.assertEqual(state.partial, "")
        self.assertEqual(state.history, [])
        self.assertEqual(state.panel.mock_calls, [])

    def test_success_commits_only_complete_question_and_answer(self):
        state = self.state
        original = list(state.history)
        state.request("问题")
        committed = original + [{"role": "user", "content": "问题"}]
        state.completed(state.request_id, "完整答案", committed)
        self.assertEqual(state.history, committed + [{"role": "assistant", "content": "完整答案"}])
        self.assertFalse(state.answer_incomplete)
        self.assertIsNone(state.failed_question)

    def test_failed_request_flushes_pending_partial_before_marking_incomplete(self):
        state = self.state
        state.request("问题")
        state.partial = "末尾片段"
        state.render_id = 31
        state.panel.reset_mock()
        state.failed(state.request_id, "断流")
        self.glib.source_remove.assert_called_with(31)
        self.assertEqual(state.render_id, 0)
        calls = state.panel.mock_calls
        self.assertLess(calls.index(unittest.mock.call.update_answer("末尾片段")),
                        calls.index(unittest.mock.call.mark_answer_incomplete("断流")))

    def test_main_reports_configuration_path_without_traceback(self):
        with patch("app.config.load_settings", side_effect=ValueError("invalid-config")), \
             patch("app.Gtk.MessageDialog") as dialog, \
             patch.object(sys, "argv", ["app.py"]):
            result = app.main()
        self.assertEqual(result, 1)
        self.assertIn(str(app.config.CONFIG_FILE), dialog.return_value.format_secondary_text.call_args.args[0])
        dialog.return_value.run.assert_called_once()
        dialog.return_value.destroy.assert_called_once()

    def test_quit_command_closes_only_current_application_without_settings(self):
        state = self.state
        state.quit, state.show_settings = MagicMock(), MagicMock()
        command = MagicMock()
        command.get_arguments.return_value = ["app.py", "--quit"]
        self.assertEqual(app.Assistant.do_command_line(state, command), 0)
        state.quit.assert_called_once()
        state.show_settings.assert_not_called()

    def test_stopped_initial_answer_retry_replaces_incomplete_bubble(self):
        state = self.state
        state.history = []
        state.request()
        state.stop_request()
        state.panel.add_question.reset_mock()
        state.retry()
        state.panel.begin_answer.assert_called_with(replace=True)
        state.panel.add_question.assert_not_called()
        self.assertEqual(state.history, [])

    def test_followup_after_success_keeps_previous_complete_answer(self):
        state = self.state
        state.request("前一个问题")
        state.completed(state.request_id, "完整回答", state.history + [{"role": "user", "content": "前一个问题"}])
        state.request("再举例")
        state.panel.begin_answer.assert_called_with(replace=False)


if __name__ == "__main__":
    unittest.main()
