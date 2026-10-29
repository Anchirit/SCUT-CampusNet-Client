"""Small user settings file; optional passwords stay in the desktop keyring."""
import json
import os
from pathlib import Path
import tempfile

import gi
gi.require_version('Secret', '1')
from gi.repository import Gio, GLib, Secret

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'mydrclient'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'mydrclient'
SCHEMA = Secret.Schema.new('io.github.Anchirit.MyDrClient', Secret.SchemaFlags.NONE,
                           {'account': Secret.SchemaAttributeType.STRING})


def load():
    try:
        data = json.loads((CONFIG / 'settings.json').read_text())
        if not isinstance(data, dict):
            return {}
        return {key: data[key] for key, kind in (('account', str), ('interface', str),
                                                ('remember', bool), ('background', bool))
                if isinstance(data.get(key), kind)}
    except (OSError, ValueError, TypeError):
        return {}


def save(account='', interface='', remember=False, background=True):
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix='.settings-', dir=CONFIG)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump({'account': account, 'interface': interface, 'remember': remember, 'background': background},
                  stream, ensure_ascii=False)
        stream.write('\n')
    os.replace(name, CONFIG / 'settings.json')


def record(kind, message):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = STATE / 'client.log'
    if path.exists() and path.stat().st_size > 96 * 1024:
        path.unlink()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(json.dumps({'event': kind, 'message': message}, ensure_ascii=False) + '\n')


class Keyring:
    def __init__(self):
        self.cancel = Gio.Cancellable()

    def _unlocked(self, action, failed):
        """Do not open a blocking unlock prompt while the user is connecting."""
        def collection_ready(source, result, unused):
            try:
                collection = Secret.Collection.for_alias_finish(result)
                if collection is None or collection.get_locked():
                    failed('系统密钥环未创建或已锁定，请在“密码和密钥”中解锁后再保存。')
                else:
                    action()
            except GLib.Error:
                failed('系统密钥环不可用，当前仍可手动输入密码连接。')
        def service_ready(source, result, unused):
            try:
                service = Secret.Service.get_finish(result)
                Secret.Collection.for_alias(service, 'default', Secret.CollectionFlags.NONE,
                                            self.cancel, collection_ready, None)
            except GLib.Error:
                failed('系统密钥环不可用，当前仍可手动输入密码连接。')
        Secret.Service.get(Secret.ServiceFlags.NONE, self.cancel, service_ready, None)

    def lookup(self, account, callback):
        def complete(source, result, unused):
            try:
                callback(Secret.password_lookup_finish(result) or '', '')
            except GLib.Error as error:
                callback('', '系统密钥环不可用；仍可手动输入密码连接。')
        self._unlocked(lambda: Secret.password_lookup(SCHEMA, {'account': account}, self.cancel, complete, None),
                       lambda error: callback('', error))

    def store(self, account, password, callback):
        def complete(source, result, unused):
            try:
                Secret.password_store_finish(result)
                callback('')
            except GLib.Error:
                callback('连接已成功，但系统密钥环未能保存密码。')
        self._unlocked(lambda: Secret.password_store(SCHEMA, {'account': account}, Secret.COLLECTION_DEFAULT,
                              'MyDrClient 校园网', password, self.cancel, complete, None), callback)

    def clear(self, account, callback):
        def complete(source, result, unused):
            try:
                Secret.password_clear_finish(result)
                callback('')
            except GLib.Error:
                callback('系统密钥环中的旧密码未能删除，请在系统“密码和密钥”中检查。')
        self._unlocked(lambda: Secret.password_clear(SCHEMA, {'account': account}, self.cancel, complete, None), callback)
