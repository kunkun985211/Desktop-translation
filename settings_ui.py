"""首次连接与后续修改共用的 API 设置窗口。"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

import config
from api_client import DEFAULT_BASE_URL, DEFAULT_MODEL
from ui import label, style


class SettingsWindow(Gtk.Window):
    def __init__(self, settings, has_saved_key, on_save):
        super().__init__(title='划词助手 · 设置')
        self.initial = dict(settings)
        self.has_saved_key = has_saved_key
        self.on_save = on_save
        self._filling = False
        style(self, 'assistant')
        self.set_default_size(560, 660)
        self.set_border_width(20)
        self.set_position(Gtk.WindowPosition.CENTER)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)
        root.pack_start(label('连接你的阅读助手', 'heading'), False, False, 0)
        root.pack_start(label('选择 API 服务，保存后即可划词翻译和解释。', 'muted'), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(260)
        root.pack_start(scroll, True, True, 0)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(form)

        self.provider = Gtk.ComboBoxText()
        for name in ('deepseek', 'openai_compatible'):
            self.provider.append(name, config.provider_label(name))
        self._field(form, 'API 类型', self.provider)
        self.base_url = Gtk.Entry()
        self.base_url.set_placeholder_text('https://api.example.com/v1')
        self._field(form, '服务地址（Base URL）', self.base_url)
        self.model = Gtk.Entry()
        self.model.set_placeholder_text('填写平台提供的模型名称')
        self._field(form, '模型', self.model)
        self.provider_hint = label('', 'muted')
        self.provider_hint.set_line_wrap(True)
        self.provider_hint.set_max_width_chars(52)
        form.pack_start(self.provider_hint, False, False, 0)
        self.tracking_id = Gtk.Entry()
        self.tracking_id.set_placeholder_text('可选，平台分配的账号或密钥标识')
        self._field(form, 'Tracking ID', self.tracking_id)
        tracking_note = label('仅在本机保存，用于区分密钥；不参与 API 鉴权。', 'muted')
        tracking_note.set_line_wrap(True)
        form.pack_start(tracking_note, False, False, 0)
        self.key = Gtk.Entry()
        self.key.set_visibility(False)
        self.key.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.key.set_activates_default(True)
        self._field(form, 'API Key', self.key)
        self.key_hint = label('', 'muted')
        self.key_hint.set_line_wrap(True)
        self.key_hint.set_max_width_chars(52)
        form.pack_start(self.key_hint, False, False, 0)
        self.automatic = Gtk.CheckButton(label='登录桌面后自动启动')
        self.automatic.set_active(config.AUTOSTART_FILE.exists())
        form.pack_start(self.automatic, False, False, 4)

        self.feedback = label('', 'error')
        self.feedback.set_no_show_all(True)
        self.feedback.set_line_wrap(True)
        self.feedback.set_max_width_chars(52)
        root.pack_start(self.feedback, False, False, 0)
        note = label('Key 保存到系统密钥环。仅点击功能或发送追问时调用 API。', 'muted')
        note.set_line_wrap(True)
        root.pack_start(note, False, False, 0)
        self.save = style(Gtk.Button(label='保存并开始划词'), 'primary')
        self.save.set_can_default(True)
        self.save.connect('clicked', self._save)
        root.pack_start(self.save, False, False, 0)
        self.set_default(self.save)

        self.provider.set_active_id(settings['provider'])
        self._fill(settings)
        self.provider.connect('changed', self._provider_changed)
        self.base_url.connect('changed', self._identity_changed)
        self.tracking_id.connect('changed', self._identity_changed)

    @staticmethod
    def _field(form, title, widget):
        form.pack_start(label(title), False, False, 0)
        form.pack_start(widget, False, False, 0)

    def _fill(self, settings):
        self._filling = True
        self.base_url.set_text(settings['base_url'])
        self.model.set_text(settings['model'])
        self.tracking_id.set_text(settings['tracking_id'])
        self.key.set_text('')
        self._filling = False
        self.provider_hint.set_text(
            '默认 DeepSeek Flash，关闭思考模式。'
            if settings['provider'] == 'deepseek'
            else '支持 Chat Completions 流式接口；地址和模型请以服务平台为准。')
        self._identity_changed()

    def _provider_changed(self, *_):
        provider = self.provider.get_active_id()
        if provider == self.initial['provider']:
            values = self.initial
        else:
            values = {'provider': provider, 'tracking_id': '',
                      'base_url': DEFAULT_BASE_URL if provider == 'deepseek' else 'https://api.openai.com/v1',
                      'model': DEFAULT_MODEL if provider == 'deepseek' else ''}
        self._fill(values)
        self.feedback.set_text('')
        self.feedback.hide()

    def values(self):
        return dict(self.initial, provider=self.provider.get_active_id(),
                    base_url=self.base_url.get_text(), model=self.model.get_text(),
                    tracking_id=self.tracking_id.get_text())

    def _identity_changed(self, *_):
        if self._filling:
            return
        self.key.set_text('')
        try:
            same = config.credential_identity(self.values()) == config.credential_identity(self.initial)
        except ValueError:
            same = False
        if same and self.has_saved_key:
            self.key.set_placeholder_text('已保存；留空保留，输入新 Key 可替换')
            self.key_hint.set_text('当前服务的 Key 已保存。')
        else:
            self.key.set_placeholder_text('填写当前服务的 API Key')
            self.key_hint.set_text('留空时仅查找同一服务地址和 Tracking ID 下已保存的 Key。')

    def _save(self, *_):
        self.save.set_sensitive(False)
        try:
            values = config.normalize_settings(self.values())
            self.on_save(values, self.key.get_text().strip(), self.automatic.get_active())
        except (ValueError, RuntimeError) as error:
            self.feedback.set_text(str(error))
            self.feedback.show()
        except (OSError, GLib.Error):
            self.feedback.set_text('保存失败，请检查系统密钥环与配置目录权限。')
            self.feedback.show()
        else:
            self.key.set_text('')
            self.destroy()
        finally:
            self.save.set_sensitive(True)
