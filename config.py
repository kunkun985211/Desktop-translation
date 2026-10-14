"""本机配置；凭据仅保存在 GNOME Secret Service。"""
import io
import json
import os
from pathlib import Path
import stat
import tempfile

from api_client import normalize_base_url

import gi
gi.require_version('Secret', '1')
from gi.repository import Secret

APP_ID = 'io.github.selectionassistant'
PROJECT = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / '.config' / 'selection-assistant'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
AUTOSTART_FILE = Path.home() / '.config' / 'autostart' / f'{APP_ID}.desktop'
MODELS = ('deepseek-flash',)
DEFAULT_BASE_URL = 'https://api.deepseek.com'
PROVIDERS = {'deepseek': 'DeepSeek', 'openai_compatible': 'OpenAI 兼容接口'}
SCHEMA = Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE,
                           {'service': Secret.SchemaAttributeType.STRING})
ATTRIBUTES = {'service': 'deepseek'}
API_SCHEMA = Secret.Schema.new(APP_ID + '.api', Secret.SchemaFlags.NONE,
                               {name: Secret.SchemaAttributeType.STRING
                                for name in ('provider', 'base_url', 'tracking_id')})


def provider_label(provider):
    if provider not in PROVIDERS:
        raise ValueError('不支持的 API 服务，请选择 DeepSeek 或 OpenAI 兼容接口。')
    return PROVIDERS[provider]


def _single_line(value, label, limit, allow_empty=False):
    if not isinstance(value, str) or any(not character.isprintable() for character in value):
        raise ValueError(f'{label}必须为单行可见文字。')
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f'请填写{label}。')
    if len(value) > limit:
        raise ValueError(f'{label}不能超过 {limit} 个字符。')
    return value


def normalize_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('配置内容必须是 JSON 对象。')
    provider = settings.get('provider', 'deepseek')
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError('不支持的 API 服务，请选择 DeepSeek 或 OpenAI 兼容接口。')
    base_url = settings.get('base_url', DEFAULT_BASE_URL if provider == 'deepseek' else '')
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError('请填写 API 基础 URL。')
    model = _single_line(settings.get('model', MODELS[0] if provider == 'deepseek' else ''),
                         '模型名称', 256)
    tracking_id = _single_line(settings.get('tracking_id', ''), 'Tracking ID', 128, allow_empty=True)
    paused = settings.get('paused', False)
    if not isinstance(paused, bool):
        raise ValueError('配置中的 paused 必须是布尔值。')
    return {'provider': provider, 'base_url': normalize_base_url(base_url),
            'model': model, 'tracking_id': tracking_id, 'paused': paused}


def credential_identity(settings):
    normalized = normalize_settings(settings)
    return tuple(normalized[name] for name in ('provider', 'base_url', 'tracking_id'))


def _credential_target(settings):
    identity = credential_identity(load_settings() if settings is None else settings)
    # 仅原默认账号沿用旧凭据；新归属使用独立 schema，不进行跨账号回退。
    if identity == ('deepseek', DEFAULT_BASE_URL, ''):
        return SCHEMA, ATTRIBUTES
    return API_SCHEMA, dict(zip(('provider', 'base_url', 'tracking_id'), identity))


def load_settings():
    if not CONFIG_FILE.exists():
        return normalize_settings({})
    data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    return normalize_settings(data)


def _write_metadata(path, content, mode):
    """仅供 settings.json 与自启入口使用的原子写入。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix='.selection-assistant-', suffix='.tmp', dir=str(path.parent))
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_metadata(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f'设置文件不是普通文件，未修改：{path}')
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def _restore_metadata(path, snapshot):
    if _snapshot_metadata(path) == snapshot:
        return
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        content, mode = snapshot
        _write_metadata(path, content, mode)


def save_settings(settings):
    # 规范化后仅有非敏感白名单字段；任何传入的 api_key 等额外字段都不会写入。
    data = normalize_settings(settings)
    serialized = io.StringIO()
    json.dump(data, serialized, ensure_ascii=False, indent=2)
    _write_metadata(CONFIG_FILE, (serialized.getvalue() + '\n').encode('utf-8'), 0o600)


def load_key(settings=None):
    schema, attributes = _credential_target(settings)
    return Secret.password_lookup_sync(schema, attributes, None) or ''


def _validate_key(value):
    if not isinstance(value, str) or not value or any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError('API Key 必须是非空 ASCII 字符，不能含空白或控制字符。')


def store_key(value, settings=None):
    _validate_key(value)
    normalized = load_settings() if settings is None else normalize_settings(settings)
    schema, attributes = _credential_target(normalized)
    label = '划词助手 · ' + provider_label(normalized['provider']) + ' API Key'
    if not Secret.password_store_sync(schema, attributes, Secret.COLLECTION_DEFAULT,
                                      label, value, None):
        raise RuntimeError('系统密钥环未能保存密钥。')


def _check_autostart_owner():
    from install import owned_desktop
    if (AUTOSTART_FILE.exists() or AUTOSTART_FILE.is_symlink()) and not owned_desktop(AUTOSTART_FILE, PROJECT):
        raise ValueError(f'未修改其它来源的自启入口：{AUTOSTART_FILE}')


def set_autostart(enabled):
    from install import desktop_entry

    _check_autostart_owner()
    if not enabled:
        AUTOSTART_FILE.unlink(missing_ok=True)
        return
    mode = stat.S_IMODE(AUTOSTART_FILE.stat().st_mode) if AUTOSTART_FILE.exists() else 0o644
    _write_metadata(AUTOSTART_FILE, desktop_entry(PROJECT, autostart=True).encode('utf-8'), mode)


def save_connection(settings, new_key, autostart):
    """保存两项元数据后最后写凭据；失败时恢复原文件内容与权限。"""
    settings = normalize_settings(settings)
    if not isinstance(autostart, bool):
        raise ValueError('自启选项必须是布尔值。')
    key = new_key or load_key(settings)
    if not key:
        raise ValueError('请填写当前服务的 API Key；不同服务地址或 Tracking ID 不会共用密钥。')
    _validate_key(key)
    _check_autostart_owner()
    previous = {path: _snapshot_metadata(path) for path in (CONFIG_FILE, AUTOSTART_FILE)}
    attempted = []
    try:
        attempted.append(CONFIG_FILE)
        save_settings(settings)
        attempted.append(AUTOSTART_FILE)
        set_autostart(autostart)
        if new_key:
            # 成功写入密钥后不再执行任何可能失败的文件操作。
            store_key(new_key, settings)
    except Exception:
        unrestored = []
        for path in reversed(attempted):
            try:
                _restore_metadata(path, previous[path])
            except Exception:
                unrestored.append(str(path))
        if unrestored:
            raise RuntimeError('部分保存：以下设置文件无法恢复，请重新打开设置检查：' + '、'.join(unrestored)) from None
        raise
    return key
