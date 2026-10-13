"""Storage selection, verified migration and process-owned encrypted sessions."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time
from vault import Database, VaultError

class Runtime:
    def __init__(self, client):
        self.c = client
        self.raw_read, self.raw_write = client.read_json, client.write_json
        self.legacy_security, self.legacy_require = client.security_action, client.require_session
        self.path = client.STATE / 'vault.db'
        self.mode_path = client.CONFIG / 'storage-mode.json'
        self.db = None
        self.token = ''
        self.deadline = 0
        self.timeout = 60
        client.read_json, client.write_json = self.read, self.write
        client.security_action, client.require_session = self.security, self.require
        client.delete_document = self.delete
        client.document_exists = lambda p: self.read(p, None) is not None

    def encrypted(self):
        return self.path.exists() or self.path.is_symlink() or bool(self.raw_read(self.mode_path, {}).get('encrypted'))

    def key(self, path):
        path = Path(path)
        if path.parent == self.c.CONFIG:
            return 'config/' + path.name
        if path.parent == self.c.STATE:
            return 'state/' + path.name
        raise self.c.ClientError('Invalid storage path.')

    def read(self, path, fallback):
        if self.encrypted():
            if self.db is None:
                raise self.c.ClientError('Vault is locked.', locked=True)
            return self.db.get(self.key(path), fallback)
        return self.raw_read(path, fallback)

    def write(self, path, data):
        if self.encrypted():
            if self.db is None:
                raise self.c.ClientError('Vault is locked.', locked=True)
            return self.db.put(self.key(path), data)
        return self.raw_write(path, data)

    def delete(self, path):
        if self.encrypted():
            if self.db is None:
                raise self.c.ClientError('Vault is locked.', locked=True)
            self.db.delete(self.key(path))
        else:
            path.unlink(missing_ok=True)

    def close(self):
        if self.db:
            self.db.close()
        self.db = None
        self.token = ''
        self.deadline = 0

    def require(self, data):
        if not self.encrypted():
            if not self.raw_read(self.mode_path, {}).get('standard'):
                raise self.c.ClientError('Choose your storage mode first.', locked=True)
            return self.legacy_require(data)
        if self.db is None or time.monotonic() >= self.deadline or not hmac.compare_digest(str(data.get('session', '')), self.token):
            if self.db and time.monotonic() >= self.deadline:
                self.close()
            raise self.c.ClientError('Vault is locked. Enter your master password.', locked=True)

    def status(self):
        if self.encrypted():
            return {'encrypted': True, 'enabled': True, 'timeout': self.timeout}
        legacy = self.raw_read(self.c.CONFIG / 'incognito.json', {})
        return {'encrypted': False, 'enabled': bool(legacy.get('enabled')), 'timeout': legacy.get('timeout', 60),
                'setup': not self.raw_read(self.mode_path, {}).get('standard'), 'legacy_lock': bool(legacy.get('enabled'))}

    def validate_password(self, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise self.c.ClientError('Choose a master password of 8–128 characters.')

    def safe_path(self, path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise self.c.ClientError('Vault file must be owned by you with permissions 600; symlinks are not allowed.')

    def attempt(self, password):
        attempts_path = self.c.STATE / 'vault-attempts.json'
        attempts = self.raw_read(attempts_path, {})
        if attempts.get('next', 0) > time.time():
            raise self.c.ClientError('Please wait before trying your password again.')
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise self.c.ClientError('Enter your master password.')
        self.safe_path(self.path)
        try:
            db = Database(self.path, password)
        except VaultError:
            count = attempts.get('count', 0) + 1
            self.raw_write(attempts_path, {'count': count, 'next': time.time() + min(30, 2 ** min(count, 5))})
            raise
        self.raw_write(attempts_path, {})
        return db

    def session(self):
        self.timeout = int(self.db.get('security', {'timeout': 60})['timeout'])
        self.deadline = time.monotonic() + self.timeout
        self.token = secrets.token_urlsafe(32)
        return {**self.status(), 'session': self.token}

    def legacy_paths(self):
        config = ['accounts.json', 'visibility.json', 'contacts.json', 'number-labels.json', 'contacts-import.json', 'incognito.json']
        state = ['read-state.json', 'send-ledger.json', 'incognito-session.json', 'incognito-attempts.json']
        return [self.c.CONFIG / n for n in config] + [self.c.STATE / n for n in state] + list(self.c.STATE.glob('numbers-*.json'))

    def sync_dir(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def cleanup(self):
        # Only remove the exact legacy files whose content was verified and encrypted.
        pending = self.db.get('migration-files', {})
        for key, digest in pending.items():
            group, name = key.split('/', 1)
            if '/' in name or group not in ('config', 'state'):
                raise self.c.ClientError('Invalid migration manifest.')
            path = (self.c.CONFIG if group == 'config' else self.c.STATE) / name
            if not path.exists() and not path.is_symlink():
                continue
            data = self.raw_read(path, None)
            if self.digest(data) != digest:
                raise self.c.ClientError('A legacy file changed during migration. Keep it safe and resolve the conflict before unlocking.')
            path.unlink()
        self.sync_dir(self.c.CONFIG)
        self.sync_dir(self.c.STATE)
        self.db.put('migration-files', {})

    @staticmethod
    def digest(data):
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def migrate(self, data):
        if self.encrypted():
            raise self.c.ClientError('The vault already exists.')
        password = data.get('password', '')
        self.validate_password(password)
        legacy = self.raw_read(self.c.CONFIG / 'incognito.json', {})
        if legacy.get('enabled'):
            self.c.check_password(data.get('current_password', ''), legacy)
        self.c.private_dir(self.c.CONFIG)
        self.c.private_dir(self.c.STATE)
        temporary = self.c.STATE / ('.vault-' + secrets.token_hex(12) + '.db')
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        db = None
        try:
            documents = {self.key(p): self.raw_read(p, None) for p in self.legacy_paths() if p.exists() or p.is_symlink()}
            db = Database(temporary, password, create=True)
            db.query('BEGIN IMMEDIATE')
            for key, value in documents.items():
                db.put(key, value)
            db.put('security', {'timeout': legacy.get('timeout', 60)})
            db.put('migration-files', {key: self.digest(value) for key, value in documents.items()})
            db.query('COMMIT')
            db.close()
            db = Database(temporary, password)
            for key, value in documents.items():
                if db.get(key, None) != value:
                    raise self.c.ClientError('Migration verification failed. Original files were retained.')
            if db.query('PRAGMA integrity_check') != ['ok'] or db.query('PRAGMA cipher_integrity_check'):
                raise self.c.ClientError('Vault integrity verification failed. Original files were retained.')
            db.close(); db = None
            # Atomic no-clobber publication; never replace an existing vault.
            os.link(temporary, self.path)
            self.sync_dir(self.c.STATE)
            self.raw_write(self.mode_path, {'encrypted': True})
            self.db = Database(self.path, password)
            self.cleanup()
            return self.session()
        except Exception:
            self.close()
            raise
        finally:
            if db:
                db.close()
            temporary.unlink(missing_ok=True)

    def security(self, data):
        action = data['action']
        if action == 'security_status':
            return self.status()
        if action == 'use_standard':
            if self.encrypted():
                raise self.c.ClientError('An encrypted vault already exists.')
            self.raw_write(self.mode_path, {'standard': True})
            return self.status()
        if action == 'start_encryption':
            self.require(data)
            return {**self.status(), 'setup': True}
        if action == 'create_vault':
            with self.c.lock('vault-migration'):
                return self.migrate(data)
        if not self.encrypted():
            return {**self.legacy_security(data), 'encrypted': False}
        if action == 'unlock':
            self.close()
            try:
                self.db = self.attempt(data.get('password', ''))
                self.cleanup()
                return self.session()
            except Exception:
                self.close()
                raise
        self.require(data)
        if action == 'touch_session':
            self.deadline = time.monotonic() + self.timeout
            return {}
        if action == 'configure_security':
            if data.get('enabled') is False:
                raise self.c.ClientError('Encrypted storage always requires a password.')
            timeout = int(data.get('timeout', 60))
            if not 5 <= timeout <= 3600:
                raise self.c.ClientError('Choose an idle timeout between 5 and 3600 seconds.')
            check = self.attempt(data.get('current_password', ''))
            check.close()
            password = data.get('password', '')
            if password:
                self.validate_password(password)
                self.db.key(password, rekey=True)
            self.db.put('security', {'timeout': timeout})
            return self.session()
        raise self.c.ClientError('Unknown security action.')
