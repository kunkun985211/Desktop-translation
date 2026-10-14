"""安装入口测试全部使用临时目录，不安装系统包、不访问密钥环。"""

import contextlib
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import install
from gi.repository import GLib


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / '项目 %f $value `tick` "quote" \\back'
        self.project.mkdir()
        for relative in ('app.py', 'run.sh', '.venv/bin/python'):
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.paths = {
            'PROJECT': self.project,
            'LAUNCHER': self.root / 'bin/selection-assistant',
            'DESKTOP': self.root / 'applications/selection-assistant.desktop',
            'AUTOSTART': self.root / 'autostart/selection-assistant.desktop',
            'ICON': self.root / 'icons/selection-assistant.svg',
        }
        self.addCleanup(patch.stopall)
        for name, value in self.paths.items():
            patch.object(install, name, value).start()
        patch.object(config, 'PROJECT', self.project).start()
        patch.object(config, 'AUTOSTART_FILE', self.paths['AUTOSTART']).start()

    def desktop_arguments(self, path):
        content = path.read_text(encoding='utf-8')
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(content, len(content.encode('utf-8')), GLib.KeyFileFlags.NONE)
        command = keyfile.get_string('Desktop Entry', 'Exec')

        def literal_percent(match):
            self.assertEqual(match.group(1), '%', '路径中的百分号不得变成 Desktop Entry 参数占位符')
            return '%'

        command = re.sub(r'%(.)', literal_percent, command)
        success, arguments = GLib.shell_parse_argv(command)
        self.assertTrue(success)
        self.assertEqual(keyfile.get_string('Desktop Entry', 'X-SelectionAssistant-Project'), str(self.project))
        return arguments

    def test_owned_launcher_without_python39_path_readlink(self):
        launcher = self.paths['LAUNCHER']
        launcher.parent.mkdir()
        launcher.symlink_to(self.project / 'run.sh')
        with patch.object(Path, 'readlink', create=True, side_effect=AttributeError('Python 3.8 无此方法')):
            self.assertTrue(install.owned_launcher())

    def test_autostart_preserves_special_path_and_ownership(self):
        config.set_autostart(True)
        self.assertEqual(self.desktop_arguments(self.paths['AUTOSTART']),
                         [str(self.project / 'run.sh'), '--background'])
        self.assertTrue(install.owned_desktop(self.paths['AUTOSTART']))
        config.set_autostart(False)
        self.assertFalse(self.paths['AUTOSTART'].exists())

    def test_repeat_install_and_uninstall_only_own_entries(self):
        with contextlib.redirect_stdout(io.StringIO()):
            install.install()
            install.install()
            self.assertEqual(self.desktop_arguments(self.paths['DESKTOP']), [str(self.project / 'run.sh')])
            config.set_autostart(True)
            install.uninstall()
        for name in ('DESKTOP', 'LAUNCHER', 'AUTOSTART', 'ICON'):
            self.assertFalse(self.paths[name].exists(), name)
        self.assertTrue((self.project / 'app.py').exists())

    def test_foreign_launcher_is_not_overwritten(self):
        launcher = self.paths['LAUNCHER']
        launcher.parent.mkdir()
        launcher.write_text('other application', encoding='utf-8')
        with self.assertRaises(SystemExit):
            install.install()
        self.assertEqual(launcher.read_text(), 'other application')
        self.assertFalse(self.paths['DESKTOP'].exists())

    def test_foreign_autostart_is_not_overwritten_or_removed(self):
        path = self.paths['AUTOSTART']
        path.parent.mkdir()
        original = '[Desktop Entry]\nName=其它应用\n'
        path.write_text(original, encoding='utf-8')
        for enabled in (True, False):
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                config.set_autostart(enabled)
            self.assertEqual(path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
