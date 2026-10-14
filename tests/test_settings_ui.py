"""设置窗口回归，不访问网络或真实密钥环。"""
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from settings_ui import SettingsWindow
from ui import apply_style
from gi.repository import Gtk, GLib


class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_style()

    def setUp(self):
        self.callback = MagicMock()
        self.window = SettingsWindow(config.normalize_settings({}), True, self.callback)
        self.window.show_all()

    def tearDown(self):
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def test_initial_and_reopened_configuration(self):
        self.assertEqual(self.window.values(), config.normalize_settings({}))
        self.assertFalse(self.window.key.get_visibility())
        self.assertEqual(self.window.key.get_text(), '')
        self.assertIn('已保存', self.window.key.get_placeholder_text())
        self.window.destroy()
        saved = config.normalize_settings({'provider': 'openai_compatible',
            'base_url': 'https://example.com/v1', 'model': 'saved-model', 'tracking_id': 'account-id'})
        self.window = SettingsWindow(saved, True, self.callback)
        self.assertEqual(self.window.values(), saved)
        self.assertEqual(self.window.key.get_text(), '')

    def test_compatible_service_saves_fields_and_entered_key(self):
        self.window.provider.set_active_id('openai_compatible')
        self.window.base_url.set_text('https://example.com/v1/chat/completions')
        self.window.model.set_text('example-model')
        self.window.tracking_id.set_text('account-id')
        self.window.key.set_text('example-token')
        self.window.automatic.set_active(False)
        self.window.save.emit('clicked')
        settings, key, autostart = self.callback.call_args.args
        self.assertEqual(settings['provider'], 'openai_compatible')
        self.assertEqual(settings['base_url'], 'https://example.com/v1')
        self.assertEqual(settings['model'], 'example-model')
        self.assertEqual(settings['tracking_id'], 'account-id')
        self.assertEqual(key, 'example-token')
        self.assertFalse(autostart)

    def test_changing_identity_clears_draft_key_but_model_does_not(self):
        self.window.key.set_text('draft-token')
        self.window.model.set_text('another-model')
        self.assertEqual(self.window.key.get_text(), 'draft-token')
        self.window.tracking_id.set_text('another-account')
        self.assertEqual(self.window.key.get_text(), '')
        self.window.key.set_text('other-token')
        self.window.base_url.set_text('https://example.com/v1')
        self.assertEqual(self.window.key.get_text(), '')
        self.assertNotIn('已保存；', self.window.key.get_placeholder_text())

    def test_switching_back_restores_original_without_revealing_key(self):
        self.window.provider.set_active_id('openai_compatible')
        self.window.key.set_text('draft-token')
        self.window.provider.set_active_id('deepseek')
        self.assertEqual(self.window.values(), config.normalize_settings({}))
        self.assertEqual(self.window.key.get_text(), '')
        self.assertIn('已保存', self.window.key.get_placeholder_text())

    def test_invalid_model_does_not_save_or_close(self):
        self.window.provider.set_active_id('openai_compatible')
        self.window.save.emit('clicked')
        self.callback.assert_not_called()
        self.assertTrue(self.window.get_visible())
        self.assertTrue(self.window.save.get_sensitive())
        self.assertIn('模型', self.window.feedback.get_text())

    def test_failed_keyring_save_keeps_window_editable(self):
        self.callback.side_effect = RuntimeError('系统密钥环未能保存密钥。')
        self.window.key.set_text('replacement-token')
        self.window.save.emit('clicked')
        self.assertTrue(self.window.get_visible())
        self.assertTrue(self.window.save.get_sensitive())
        self.assertIn('密钥环', self.window.feedback.get_text())
        self.assertEqual(self.window.key.get_text(), 'replacement-token')

    def test_default_window_shows_complete_autostart_checkbox(self):
        loop = GLib.MainLoop()
        GLib.timeout_add(250, lambda: (loop.quit(), False)[1])
        loop.run()
        viewport = self.window.automatic.get_parent().get_parent()
        _, top = self.window.automatic.translate_coordinates(viewport, 0, 0)
        self.assertLessEqual(top + self.window.automatic.get_allocated_height(),
                             viewport.get_allocated_height())


if __name__ == '__main__':
    unittest.main()
