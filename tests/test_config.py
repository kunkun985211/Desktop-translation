"""配置与凭据归属测试；全部使用临时目录和模拟 Secret Service。"""

import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'configuration'
        self.path = self.directory / 'settings.json'
        self.autostart = Path(self.temporary.name) / 'autostart/assistant.desktop'
        for name, value in (('CONFIG_DIR', self.directory), ('CONFIG_FILE', self.path),
                            ('AUTOSTART_FILE', self.autostart)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        lookup = patch.object(config.Secret, 'password_lookup_sync', return_value=None)
        store = patch.object(config.Secret, 'password_store_sync', return_value=True)
        self.lookup = lookup.start()
        self.store = store.start()
        self.addCleanup(lookup.stop)
        self.addCleanup(store.stop)

    def write_settings(self, settings):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings), encoding='utf-8')

    def compatible(self, **overrides):
        settings = {'provider': 'openai_compatible', 'base_url': 'https://example.test/v1',
                    'model': 'custom/model', 'tracking_id': 'research', 'paused': False}
        settings.update(overrides)
        return settings

    def test_missing_and_legacy_settings_have_deepseek_defaults(self):
        expected = {'provider': 'deepseek', 'base_url': 'https://api.deepseek.com',
                    'model': 'deepseek-flash', 'tracking_id': '', 'paused': False}
        self.assertEqual(config.load_settings(), expected)
        self.write_settings({'model': 'deepseek-flash', 'paused': True})
        expected['paused'] = True
        self.assertEqual(config.load_settings(), expected)
        self.lookup.assert_not_called()

    def test_custom_provider_url_and_model_roundtrip(self):
        settings = self.compatible(base_url='https://example.test/v1/chat/completions/',
                                   tracking_id='  文献账号  ')
        config.save_settings(settings)
        loaded = config.load_settings()
        self.assertEqual(loaded['base_url'], 'https://example.test/v1')
        self.assertEqual(loaded['tracking_id'], '文献账号')
        self.assertEqual(loaded['model'], 'custom/model')
        self.assertEqual(loaded['provider'], 'openai_compatible')

    def test_only_whitelisted_metadata_is_written_with_private_permissions(self):
        settings = self.compatible(api_key='dummy-must-not-be-stored', password='dummy-password')
        config.save_settings(settings)
        raw = self.path.read_text(encoding='utf-8')
        self.assertNotIn('dummy-must-not-be-stored', raw)
        self.assertNotIn('dummy-password', raw)
        self.assertEqual(set(json.loads(raw)), {'provider', 'base_url', 'model', 'tracking_id', 'paused'})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_atomic_save_keeps_existing_configuration_on_failure(self):
        config.save_settings(self.compatible())
        original = self.path.read_bytes()
        with patch.object(config.json, 'dump', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                config.save_settings(self.compatible(model='new-model'))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_compatible_provider_requires_url_and_model(self):
        for missing in ('base_url', 'model'):
            for value in ('', None):
                with self.subTest(missing=missing, value=value), self.assertRaises(ValueError):
                    config.normalize_settings(self.compatible(**{missing: value}))
            settings = self.compatible()
            del settings[missing]
            with self.subTest(missing=missing, value='absent'), self.assertRaises(ValueError):
                config.normalize_settings(settings)

    def test_tracking_id_must_be_short_single_line(self):
        for value in ('a\nb', 'a\rb', 'a\tb', 'x' * 129, 'a\u2028b', 'a\x00b'):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                config.normalize_settings(self.compatible(tracking_id=value))

    def test_non_loopback_http_is_rejected_and_loopback_is_allowed(self):
        with self.assertRaises(ValueError):
            config.normalize_settings(self.compatible(base_url='http://example.test/v1'))
        local = config.normalize_settings(self.compatible(base_url='http://127.0.0.1:8000/v1'))
        self.assertEqual(local['base_url'], 'http://127.0.0.1:8000/v1')

    def test_default_deepseek_reuses_only_legacy_credentials(self):
        self.lookup.return_value = 'dummy-legacy-token'
        self.assertEqual(config.load_key({'model': 'deepseek-flash', 'paused': False}), 'dummy-legacy-token')
        self.lookup.assert_called_once_with(config.SCHEMA, config.ATTRIBUTES, None)
        config.store_key('arbitrary-token', {'model': 'deepseek-flash'})
        self.assertIs(self.store.call_args.args[0], config.SCHEMA)
        self.assertEqual(self.store.call_args.args[1], config.ATTRIBUTES)

    def test_endpoint_provider_and_tracking_id_isolate_keys(self):
        credentials = {}

        def save(schema, attributes, collection, label, value, cancellable):
            credentials[tuple(sorted(attributes.items()))] = value
            return True

        def lookup(schema, attributes, cancellable):
            return credentials.get(tuple(sorted(attributes.items())))

        self.store.side_effect = save
        self.lookup.side_effect = lookup
        original = self.compatible()
        config.store_key('dummy-account-token', original)
        self.assertEqual(config.load_key(original), 'dummy-account-token')
        for changed in (self.compatible(base_url='https://other.test/v1'),
                        self.compatible(tracking_id='different-account'),
                        self.compatible(provider='deepseek')):
            with self.subTest(changed=changed):
                self.assertEqual(config.load_key(changed), '')
        canonical = self.compatible(base_url='https://example.test/v1/chat/completions')
        self.assertEqual(config.load_key(canonical), 'dummy-account-token')
        self.assertEqual(config.credential_identity(original), config.credential_identity(canonical))

    def test_nondefault_deepseek_never_looks_up_legacy_key(self):
        for changed in ({'provider': 'deepseek', 'tracking_id': 'account-two'},
                        {'provider': 'deepseek', 'base_url': 'https://api.deepseek.com/v1'}):
            self.lookup.reset_mock()
            self.assertEqual(config.load_key(changed), '')
            self.assertIsNot(self.lookup.call_args.args[0], config.SCHEMA)
            self.assertNotEqual(self.lookup.call_args.args[1], config.ATTRIBUTES)
            self.assertEqual(self.lookup.call_count, 1)

    def test_current_settings_are_used_when_argument_is_omitted(self):
        config.save_settings(self.compatible())
        config.store_key('non-sk-token')
        attributes = self.store.call_args.args[1]
        self.lookup.return_value = 'non-sk-token'
        self.assertEqual(config.load_key(), 'non-sk-token')
        self.assertEqual(self.lookup.call_args.args[1], attributes)

    def test_api_keys_allow_printable_ascii_without_prefix_requirement(self):
        settings = self.compatible()
        config.store_key('abc.DEF_123+-=:/', settings)
        self.assertEqual(self.store.call_args.args[4], 'abc.DEF_123+-=:/')
        for value in ('', ' token', 'token ', 'to ken', 'token\n', 'token\x00', 'token\x7f', '密钥'):
            self.store.reset_mock()
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                config.store_key(value, settings)
            self.store.assert_not_called()

    def test_keyring_failure_is_not_saved_to_regular_configuration(self):
        self.store.return_value = False
        with self.assertRaises(RuntimeError):
            config.store_key('dummy-token', self.compatible())
        self.assertFalse(self.path.exists())

    def test_connection_checks_foreign_autostart_before_saving_anything(self):
        self.autostart.parent.mkdir()
        original = b'[Desktop Entry]\nName=foreign\n'
        self.autostart.write_bytes(original)
        with patch.object(config, 'save_settings') as save:
            with self.assertRaises(ValueError):
                config.save_connection(self.compatible(), 'dummy-token', True)
        save.assert_not_called()
        self.store.assert_not_called()
        self.assertEqual(self.autostart.read_bytes(), original)
        self.assertFalse(self.path.exists())

    def test_connection_file_failure_does_not_write_key(self):
        self.write_settings({'model': 'deepseek-flash', 'paused': True})
        original = self.path.read_bytes()
        with patch.object(config, 'save_settings', side_effect=OSError('write failure')):
            with self.assertRaises(OSError):
                config.save_connection(self.compatible(), 'dummy-token', True)
        self.store.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.autostart.exists())

    def test_connection_autostart_failure_restores_prior_configuration(self):
        self.write_settings({'model': 'deepseek-flash', 'paused': True})
        self.path.chmod(0o640)
        original = self.path.read_bytes()
        with patch.object(config, 'set_autostart', side_effect=OSError('autostart failure')):
            with self.assertRaises(OSError):
                config.save_connection(self.compatible(), 'dummy-token', True)
        self.store.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_connection_keyring_failure_restores_bytes_modes_and_removed_autostart(self):
        self.write_settings({'model': 'deepseek-flash', 'paused': True})
        self.path.chmod(0o640)
        config.set_autostart(True)
        self.autostart.chmod(0o600)
        original = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                    for path in (self.path, self.autostart)}
        self.store.return_value = False
        with self.assertRaises(RuntimeError):
            config.save_connection(self.compatible(), 'dummy-token', False)
        self.assertEqual(self.store.call_count, 1)
        for path, (content, mode) in original.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_connection_keyring_failure_removes_new_metadata_files(self):
        self.store.side_effect = RuntimeError('keyring failure')
        with self.assertRaises(RuntimeError):
            config.save_connection(self.compatible(), 'dummy-token', True)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.autostart.exists())

    def test_connection_reports_partial_save_if_rollback_fails(self):
        self.store.return_value = False
        with patch.object(config, '_restore_metadata', create=True, side_effect=OSError('rollback failure')):
            with self.assertRaisesRegex(RuntimeError, '部分保存'):
                config.save_connection(self.compatible(), 'dummy-token', True)

    def test_connection_writes_key_only_after_metadata_success(self):
        desired = self.compatible()

        def save(schema, attributes, collection, label, value, cancellable):
            self.assertEqual(config.load_settings(), desired)
            self.assertTrue(self.autostart.exists())
            self.assertEqual(attributes['base_url'], desired['base_url'])
            self.assertEqual(attributes['tracking_id'], desired['tracking_id'])
            self.assertEqual(value, 'dummy-token')
            return True

        self.store.side_effect = save
        self.assertEqual(config.save_connection(desired, 'dummy-token', True), 'dummy-token')
        self.assertEqual(self.store.call_count, 1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_connection_without_new_key_uses_only_target_identity(self):
        desired = self.compatible(tracking_id='new-account')
        with self.assertRaises(ValueError):
            config.save_connection(desired, '', False)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.lookup.call_args.args[1]['tracking_id'], 'new-account')
        self.lookup.return_value = 'existing-target-token'
        self.assertEqual(config.save_connection(desired, '', False), 'existing-target-token')
        self.store.assert_not_called()

    def test_autostart_atomic_failure_keeps_original_bytes(self):
        config.set_autostart(True)
        original = self.autostart.read_bytes()
        with patch.object(config.os, 'fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                config.set_autostart(True)
        self.assertEqual(self.autostart.read_bytes(), original)
        self.assertEqual(list(self.autostart.parent.iterdir()), [self.autostart])


if __name__ == '__main__':
    unittest.main()
