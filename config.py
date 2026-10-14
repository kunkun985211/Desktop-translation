"""本机配置；凭据仅保存在 GNOME Secret Service。"""
import json
import os
from pathlib import Path

import gi
gi.require_version('Secret', '1')
from gi.repository import Secret

APP_ID = 'io.github.selectionassistant'
PROJECT = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / '.config' / 'selection-assistant'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
AUTOSTART_FILE = Path.home() / '.config' / 'autostart' / f'{APP_ID}.desktop'
MODELS = ('deepseek-flash',)
SCHEMA = Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE,
                           {'service': Secret.SchemaAttributeType.STRING})
ATTRIBUTES = {'service': 'deepseek'}


def load_settings():
    if not CONFIG_FILE.exists():
        return {'model': MODELS[0], 'paused': False}
    data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    model = data.get('model', MODELS[0])
    if model not in MODELS:
        raise ValueError('配置中的模型不受支持，请在设置中选择 DeepSeek 模型。')
    return {'model': model, 'paused': bool(data.get('paused', False))}


def save_settings(settings):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # 只允许非敏感字段写入配置文件。
    data = {'model': settings['model'], 'paused': bool(settings['paused'])}
    temporary = CONFIG_FILE.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(CONFIG_FILE)


def load_key():
    return Secret.password_lookup_sync(SCHEMA, ATTRIBUTES, None) or ''


def store_key(value):
    value = value.strip()
    if not value.startswith('sk-') or any(c.isspace() for c in value):
        raise ValueError('请输入有效的 DeepSeek API Key（以 sk- 开头）。')
    if not Secret.password_store_sync(SCHEMA, ATTRIBUTES, Secret.COLLECTION_DEFAULT,
                                      '划词助手 · DeepSeek API Key', value, None):
        raise RuntimeError('系统密钥环未能保存密钥。')


def set_autostart(enabled):
    from install import desktop_entry, owned_desktop

    if (AUTOSTART_FILE.exists() or AUTOSTART_FILE.is_symlink()) and not owned_desktop(AUTOSTART_FILE, PROJECT):
        raise ValueError(f'未修改其它来源的自启入口：{AUTOSTART_FILE}')
    if not enabled:
        AUTOSTART_FILE.unlink(missing_ok=True)
        return
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(
        desktop_entry(PROJECT, autostart=True), encoding='utf-8')
