#!/usr/bin/env python3
"""Local Twilio SMS client. JSON on stdin/stdout; credentials are sent only to Twilio over HTTPS.

No shell execution, webhooks or SMS command execution. Sending requires the explicit
send action. Durable request IDs prevent a timed-out request from being sent twice.
"""
import base64
import configparser
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "bananilio"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "bananilio"
PHONE = re.compile(r"\+[1-9][0-9]{6,14}\Z")
SID = re.compile(r"AC[0-9a-fA-F]{32}\Z")
TOKEN = re.compile(r"[0-9a-fA-F]{32}\Z")


class ClientError(Exception):
    def __init__(self, message, uncertain=False, locked=False):
        super().__init__(message)
        self.uncertain = uncertain
        self.locked = locked


def private_dir(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ClientError("Storage directory must be owned by you and cannot be a symlink.")
    os.chmod(path, 0o700)


def read_json(path, fallback):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return fallback
    with os.fdopen(fd) as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ClientError("Private data file must be owned by you with permissions 600.")
        raw = f.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ClientError("Private data file is too large.")
        return json.loads(raw)


def write_json(path, data):
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def lock(name):
    private_dir(STATE)
    fd = os.open(STATE / (name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def delete_document(path):
    path.unlink(missing_ok=True)


def document_exists(path):
    return path.exists()


def account_id(sid):
    return hashlib.sha256(sid.encode()).hexdigest()[:16]


def security_config():
    return read_json(CONFIG / "incognito.json", {})


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def require_session(data):
    config = security_config()
    if not config.get("enabled"):
        return
    session = read_json(STATE / "incognito-session.json", {})
    digest = hashlib.sha256(str(data.get("session", "")).encode()).hexdigest()
    if session.get("expires", 0) <= time.time() or not hmac.compare_digest(digest, session.get("digest", "")):
        raise ClientError("Incognito is locked. Enter your password to continue.", locked=True)


def check_password(password, config):
    path = STATE / "incognito-attempts.json"
    attempts = read_json(path, {})
    if attempts.get("next", 0) > time.time():
        raise ClientError("Please wait before trying your password again.")
    if not isinstance(password, str) or len(password) > 128 or not hmac.compare_digest(password_hash(password, config["salt"]), config["hash"]):
        count = attempts.get("count", 0) + 1
        write_json(path, {"count": count, "next": time.time() + min(30, 2 ** min(count, 5))})
        raise ClientError("Incorrect password.")
    write_json(path, {})


def new_session(timeout):
    token = secrets.token_urlsafe(32)
    write_json(STATE / "incognito-session.json", {"digest": hashlib.sha256(token.encode()).hexdigest(), "expires": time.time() + timeout})
    return {"session": token, "enabled": True, "timeout": timeout}


def security_action(data):
    with lock("incognito"):
        config = security_config()
        action = data["action"]
        if action == "security_status":
            return {"enabled": bool(config.get("enabled")), "timeout": config.get("timeout", 60)}
        if action == "unlock":
            if not config.get("enabled"):
                return {"enabled": False, "timeout": 60}
            check_password(data.get("password", ""), config)
            return new_session(config["timeout"])
        require_session(data)
        if action == "touch_session":
            if config.get("enabled"):
                session = read_json(STATE / "incognito-session.json", {})
                session["expires"] = time.time() + config["timeout"]
                write_json(STATE / "incognito-session.json", session)
            return {}
        if action == "configure_security":
            if config.get("enabled"):
                check_password(data.get("current_password", ""), config)
            if data.get("enabled") is False:
                write_json(CONFIG / "incognito.json", {"enabled": False})
                write_json(STATE / "incognito-session.json", {})
                return {"enabled": False, "timeout": 60}
            password = data.get("password", "")
            timeout = int(data.get("timeout", 60))
            if not isinstance(password, str) or not 8 <= len(password) <= 128:
                raise ClientError("Choose a password of 8–128 characters.")
            if not 5 <= timeout <= 3600:
                raise ClientError("Choose an idle timeout between 5 and 3600 seconds.")
            salt = secrets.token_hex(16)
            write_json(CONFIG / "incognito.json", {"enabled": True, "salt": salt, "hash": password_hash(password, salt), "timeout": timeout})
            return new_session(timeout)
    raise ClientError("Unknown security action.")


def accounts():
    return read_json(CONFIG / "accounts.json", {"accounts": []})["accounts"]


def validate_account(data):
    sid, token = str(data.get("sid", "")).strip(), str(data.get("token", "")).strip()
    name = str(data.get("name", "")).strip()
    if not SID.fullmatch(sid) or not TOKEN.fullmatch(token):
        raise ClientError("Enter a valid Account SID (AC…) and 32-character Auth Token.")
    if not name or len(name) > 60:
        raise ClientError("Enter an account name (1–60 characters).")
    return {"id": account_id(sid), "name": name, "sid": sid, "token": token}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api(account, resource, params=None, post=False):
    base = "/2010-04-01/Accounts/" + account["sid"] + "/"
    if resource not in ("Messages.json", "IncomingPhoneNumbers.json"):
        raise ClientError("Unsupported Twilio resource.")
    url = "https://api.twilio.com" + base + resource
    encoded = urlencode(params or {}).encode()
    if not post and encoded:
        url += "?" + encoded.decode()
    auth = base64.b64encode((account["sid"] + ":" + account["token"]).encode()).decode()
    req = Request(url, data=encoded if post else None, headers={
        "Authorization": "Basic " + auth,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "User-Agent": "Bananilio/0.1",
    })
    try:
        with build_opener(NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ClientError("Twilio response is too large.", uncertain=post)
            return json.loads(raw)
    except HTTPError as e:
        try:
            detail = json.loads(e.read(8192))
            message = str(detail.get("message", "Request rejected"))[:250]
            code = str(detail.get("code", e.code))
        except (ValueError, AttributeError):
            message, code = "Request rejected", str(e.code)
        # A server error after POST does not prove that no message was created.
        raise ClientError("Twilio " + code + ": " + message, uncertain=post and e.code >= 500) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ClientError("Cannot reach Twilio or read its response. Check your connection.", uncertain=post) from None


def numbers(account, fresh=False):
    path = STATE / ("numbers-" + account["id"] + ".json")
    cached = read_json(path, {})
    if not fresh and time.time() - cached.get("at", 0) < 300:
        return cached.get("numbers", [])
    result, params = [], {"PageSize": 100}
    for _ in range(10):
        page = api(account, "IncomingPhoneNumbers.json", params)
        for item in page.get("incoming_phone_numbers", []):
            if item.get("capabilities", {}).get("sms") and PHONE.fullmatch(item.get("phone_number", "")):
                result.append({"number": item["phone_number"], "name": item.get("friendly_name", "")})
        next_uri = page.get("next_page_uri")
        if not next_uri:
            break
        from urllib.parse import parse_qs
        parsed = urlsplit(next_uri)
        expected = "/2010-04-01/Accounts/" + account["sid"] + "/IncomingPhoneNumbers.json"
        if parsed.path != expected or (parsed.netloc and parsed.netloc != "api.twilio.com"):
            raise ClientError("Unexpected phone-number pagination response.")
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    else:
        raise ClientError("Too many phone numbers to load in one request.")
    result.sort(key=lambda n: n["number"])
    write_json(path, {"at": time.time(), "numbers": result})
    return result


def public_account(account):
    return {"id": account["id"], "name": account["name"], "sid_suffix": account["sid"][-6:]}


def visible_numbers(aid, found):
    selected = read_json(CONFIG / "visibility.json", {}).get(aid)
    return {n["number"] for n in found if selected is None or n["number"] in selected}


def contacts_store():
    return read_json(CONFIG / "contacts.json", {"books": {}, "shared": {}})


def book_key(store, aid, number):
    return aid + ":" + ("shared" if store["shared"].get(aid, False) else number)


def contact_list(store, aid, number):
    entries = store["books"].get(book_key(store, aid, number), {})
    return sorted(entries.values(), key=lambda c: (c["name"].casefold(), c["number"]))


def contact_action(data):
    aid, number = str(data.get("account", "")), str(data.get("from", ""))
    account = next((a for a in accounts() if a["id"] == aid), None)
    if not account or number not in visible_numbers(aid, numbers(account)):
        raise ClientError("Select a visible number for this address book.")
    with lock("contacts"):
        store = contacts_store()
        key = book_key(store, aid, number)
        book = store["books"].setdefault(key, {})
        action = data["action"]
        if action == "share_contacts":
            shared_key = aid + ":shared"
            if data.get("shared") and shared_key not in store["books"]:
                store["books"][shared_key] = dict(book)
            store["shared"][aid] = bool(data.get("shared"))
        elif action == "save_contact":
            target, name = str(data.get("number", "")).strip(), str(data.get("name", "")).strip()
            original = str(data.get("original", ""))
            if not name or len(name) > 120 or any(ord(c) < 32 for c in name + target):
                raise ClientError("Enter a contact name of 1–120 characters.")
            if original and original not in book:
                raise ClientError("This contact no longer exists. Refresh your address book.")
            if not PHONE.fullmatch(target) and target != original:
                raise ClientError("Enter a complete international number starting with +.")
            if target in book and target != original:
                raise ClientError("This number already exists in this address book. Edit that contact instead.")
            if len(book) >= 5000 and not original:
                raise ClientError("This address book has reached its 5000-contact limit.")
            if original:
                book.pop(original)
            book[target] = {"name": name, "number": target}
        elif action == "delete_contact":
            target = str(data.get("number", ""))
            if target not in book:
                raise ClientError("This contact no longer exists.")
            del book[target]
        else:
            raise ClientError("Unknown contact action.")
        write_json(CONFIG / "contacts.json", store)
    return {}


def import_contacts():
    path = CONFIG / "contacts-import.json"
    source = read_json(path, None)
    if not isinstance(source, dict) or len(source) > 5000 or any(not isinstance(v, dict) for v in source.values()):
        raise ClientError("No valid staged address book to import.")
    imported = {}
    for number, info in source.items():
        name = str(info.get("name", "")).strip() or number
        if not number or len(number) > 64 or len(name) > 120 or any(ord(c) < 32 for c in number + name):
            raise ClientError("The source address book contains an invalid name or number. Nothing was imported.")
        imported[number] = {"number": number, "name": name}
    targets = [(a["id"], n["number"]) for a in accounts() for n in numbers(a)]
    if not targets:
        raise ClientError("Connect an account with phone numbers before importing contacts.")
    added = 0
    with lock("contacts"):
        store = contacts_store()
        for aid, number in targets:
            book = store["books"].setdefault(aid + ":" + number, {})
            for key, value in imported.items():
                if key not in book:
                    book[key] = dict(value)
                    added += 1
            if len(book) > 5000:
                raise ClientError("Import would exceed 5000 contacts in an address book. Nothing was saved.")
        write_json(CONFIG / "contacts.json", store)
    delete_document(path)
    return {"imported_contacts": len(imported), "books": len(targets), "added_entries": added}


def set_visible_numbers(data):
    aid, selected = data.get("account"), data.get("numbers")
    account = next((a for a in accounts() if a["id"] == aid), None)
    if not account or not isinstance(selected, list) or not all(isinstance(n, str) for n in selected):
        raise ClientError("Select an account and its visible numbers.")
    owned = {n["number"] for n in numbers(account)}
    if not set(selected) <= owned:
        raise ClientError("Select only numbers belonging to this account.")
    with lock("visibility"):
        path = CONFIG / "visibility.json"
        config = read_json(path, {})
        config[aid] = sorted(set(selected))
        write_json(path, config)
    return {}


def set_number_label(data):
    aid, number, label = (str(data.get(k, "")).strip() for k in ("account", "number", "label"))
    account = next((a for a in accounts() if a["id"] == aid), None)
    if not account or number not in {n["number"] for n in numbers(account)}:
        raise ClientError("Select one of your account's phone numbers.")
    if len(label) > 40 or any(ord(c) < 32 for c in label):
        raise ClientError("Use a label of up to 40 characters.")
    with lock("number-labels"):
        path = CONFIG / "number-labels.json"
        labels = read_json(path, {})
        labels[aid + ":" + number] = label
        write_json(path, labels)
    return {}


def normalize_message(m, aid):
    sid = str(m.get("sid", ""))
    return {"key": aid + ":" + sid, "sid": sid, "account": aid,
            "from": m.get("from", ""), "to": m.get("to", ""), "body": m.get("body") or "",
            "status": m.get("status", ""), "inbound": m.get("direction") == "inbound",
            "date": m.get("date_sent") or m.get("date_created") or "",
            "segments": m.get("num_segments"), "media": int(m.get("num_media") or 0),
            "error_code": m.get("error_code")}


def fetch_account(account, fresh=False, limit=50):
    output = public_account(account)
    try:
        output["numbers"] = numbers(account, fresh)
        page = api(account, "Messages.json", {"PageSize": limit})
        owned = {n["number"] for n in output["numbers"]}
        output["messages"] = [normalize_message(m, account["id"]) for m in page.get("messages", [])
                              if m.get("from") in owned or m.get("to") in owned]
        output["error"] = ""
    except ClientError as e:
        output.update(error=str(e), numbers=output.get("numbers", []), messages=[])
    return output


def update_unread(items):
    with lock("read-state"):
        path = STATE / "read-state.json"
        state = read_json(path, {})
        new_count = 0
        for a in items:
            if a.get("error"):
                continue
            incoming = [m for m in a["messages"] if m["inbound"]]
            keys = {m["key"] for m in incoming}
            old = state.get(a["id"])
            seen = set(old.get("seen", [])) if old else keys
            unread = set(old.get("unread", [])) if old else set()
            newly_seen = keys - seen
            new_count += len(newly_seen)
            unread |= newly_seen
            # State contains IDs only, never message bodies or credentials.
            seen_list = list(dict.fromkeys([m["key"] for m in incoming] + (old or {}).get("seen", [])))[:2000]
            unread &= set(seen_list)
            state[a["id"]] = {"seen": seen_list, "unread": sorted(unread)}
            for m in a["messages"]:
                m["unread"] = m["key"] in unread
                m["new_unread"] = m["key"] in newly_seen
        write_json(path, state)
        return new_count, sum(len(s.get("unread", [])) for s in state.values())


def snapshot(data):
    loaded = accounts()
    limit = max(20, min(200, int(data.get("limit", 50))))
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(lambda a: fetch_account(a, bool(data.get("fresh_numbers")), limit), loaded))
    update_unread(items)
    new_count, unread = 0, 0
    for a in items:
        visible = visible_numbers(a["id"], a["numbers"])
        for n in a["numbers"]:
            n["visible"] = n["number"] in visible
        a["messages"] = [m for m in a["messages"] if (m["to"] if m["inbound"] else m["from"]) in visible]
        new_count += sum(m.get("new_unread", False) for m in a["messages"])
        unread += sum(m.get("unread", False) for m in a["messages"])
    contacts = contacts_store()
    labels = read_json(CONFIG / "number-labels.json", {})
    for a in items:
        for n in a["numbers"]:
            n["label"] = labels.get(a["id"] + ":" + n["number"], "")
        a["contacts_shared"] = bool(contacts["shared"].get(a["id"], False))
        a["contact_books"] = {n["number"]: contact_list(contacts, a["id"], n["number"]) for n in a["numbers"] if n["visible"]}
    return {"accounts": items, "new_count": new_count, "unread": unread, "at": time.time(),
            "contact_import_pending": document_exists(CONFIG / "contacts-import.json")}


def save_account(data, validate=True):
    account = validate_account(data)
    if validate:
        found = numbers(account, fresh=True)
    else:
        found = []
    with lock("accounts"):
        loaded = accounts()
        loaded = [a for a in loaded if a["id"] != account["id"]]
        if len(loaded) >= 20:
            raise ClientError("Maximum of 20 accounts reached.")
        loaded.append(account)
        write_json(CONFIG / "accounts.json", {"accounts": loaded})
    return {"account": public_account(account), "number_count": len(found)}


def disconnect_account(data):
    aid = str(data.get("account", ""))
    if not re.fullmatch(r"[0-9a-f]{16}", aid):
        raise ClientError("Select a connected account.")
    with lock("accounts"):
        loaded = accounts()
        if not any(a["id"] == aid for a in loaded):
            raise ClientError("This account is not connected.")
        write_json(CONFIG / "accounts.json", {"accounts": [a for a in loaded if a["id"] != aid]})
    with lock("visibility"):
        path = CONFIG / "visibility.json"
        config = read_json(path, {})
        config.pop(aid, None)
        write_json(path, config)
    with lock("read-state"):
        path = STATE / "read-state.json"
        config = read_json(path, {})
        config.pop(aid, None)
        write_json(path, config)
    delete_document(STATE / ("numbers-" + aid + ".json"))
    return {}


def send(data):
    aid, sender, to, body = [str(data.get(k, "")) for k in ("account", "from", "to", "body")]
    rid = str(data.get("request_id", ""))
    if not PHONE.fullmatch(sender) or not PHONE.fullmatch(to):
        raise ClientError("Use phone numbers with country code, for example +12025550120.")
    if not body.strip() or len(body) > 1600:
        raise ClientError("Enter a message of 1–1600 characters.")
    if not re.fullmatch(r"[A-Za-z0-9-]{16,80}", rid):
        raise ClientError("Missing or invalid send request ID.")
    account = next((a for a in accounts() if a["id"] == aid), None)
    if not account:
        raise ClientError("Select an existing account.")
    digest = hashlib.sha256(json.dumps([aid, sender, to, body]).encode()).hexdigest()
    with lock("send"):
        path = STATE / "send-ledger.json"
        ledger = read_json(path, {})
        entry = ledger.get(rid)
        if entry:
            if entry["digest"] != digest:
                raise ClientError("This request ID belongs to a different message.")
            if entry["state"] == "accepted":
                return entry["result"]
            raise ClientError("This send was already attempted. Check Sent before composing another message.", uncertain=True)
        if sender not in visible_numbers(aid, numbers(account, fresh=True)):
            raise ClientError("The sending number must be visible and SMS-enabled on this account.")
        ledger = {k: v for k, v in ledger.items() if time.time() - v.get("at", 0) < 30 * 86400}
        ledger[rid] = {"digest": digest, "state": "started", "at": time.time()}
        write_json(path, ledger)
        try:
            m = api(account, "Messages.json", {"From": sender, "To": to, "Body": body}, post=True)
            if not m.get("sid"):
                raise ClientError("Twilio did not return a message ID. Check Sent.", uncertain=True)
            result = {"sid": m["sid"], "status": m.get("status", "accepted"), "from": sender, "to": to}
            ledger[rid].update(state="accepted", result=result)
            write_json(path, ledger)
            return result
        except ClientError as e:
            ledger[rid]["state"] = "unknown" if e.uncertain else "rejected"
            write_json(path, ledger)
            raise


def dispatch(data):
    action = data.get("action", "snapshot")
    if action in ("security_status", "unlock", "configure_security", "touch_session", "create_vault", "use_standard", "start_encryption"):
        return security_action(data)
    require_session(data)
    if action == "snapshot":
        return snapshot(data)
    if action == "add_account":
        return save_account(data)
    if action == "disconnect_account":
        return disconnect_account(data)
    if action in ("save_contact", "delete_contact", "share_contacts"):
        return contact_action(data)
    if action == "import_contacts":
        return import_contacts()
    if action == "set_visible_numbers":
        return set_visible_numbers(data)
    if action == "set_number_label":
        return set_number_label(data)
    if action == "send":
        return send(data)
    if action == "mark_read":
        keys = data.get("keys", [])
        if not isinstance(keys, list) or len(keys) > 4000:
            raise ClientError("Invalid read-state request.")
        with lock("read-state"):
            path = STATE / "read-state.json"
            state = read_json(path, {})
            for entry in state.values():
                entry["unread"] = [k for k in entry.get("unread", []) if k not in keys]
            write_json(path, state)
        return {}
    raise ClientError("Unknown action.")


def demo(data):
    """Synthetic data only: this path never reads storage or calls Twilio."""
    action = data.get("action", "snapshot")
    if action == "send":
        return {"demo": True, "sid": "DEMO-SIMULATED", "status": "simulated"}
    if action == "add_account":
        return {"demo": True, "number_count": 2}
    if action == "mark_read":
        return {"demo": True}
    messages = []
    for i, (incoming, body) in enumerate([
        (True, "Demo: your parcel is ready for collection. This is a fictional message."),
        (False, "Thanks! I will collect it this afternoon."),
        (True, "Demo: our meeting moved to 15:30. Does that work for you?"),
        (False, "Yes, see you at 15:30."),
    ]):
        messages.append({"key": "demo-main:DEMO" + str(i), "sid": "DEMO" + str(i), "account": "demo-main",
                         "from": "+12025550120" if incoming else "+12025550101",
                         "to": "+12025550101" if incoming else "+12025550120", "body": body,
                         "status": "received" if incoming else "delivered", "inbound": incoming,
                         "date": "Tue, 01 Sep 2026 10:0" + str(9-i) + ":00 +0000", "unread": i == 0,
                         "segments": "1", "media": 0, "error_code": None})
    return {"demo": True, "at": time.time(), "new_count": 0, "unread": 1, "accounts": [
        {"id": "demo-main", "name": "Demo · Studio", "sid_suffix": "DEMO01", "error": "",
         "numbers": [{"number": "+12025550101", "name": "Studio"}, {"number": "+12025550102", "name": "Team"}], "messages": messages},
        {"id": "demo-shop", "name": "Demo · Shop", "sid_suffix": "DEMO02", "error": "",
         "numbers": [{"number": "+12025550103", "name": "Customer support"}], "messages": []}]}


def main():
    import resource
    import select
    import ctypes
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    # Prevent ordinary same-UID ptrace/core collection of the key-owning helper.
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE
    args = sys.argv[1:]
    if args not in ([], ["--demo"], ["--serve"], ["--serve", "--demo"]):
        print(json.dumps({"ok": False, "error": "Unknown command-line option."}))
        return
    demo_mode = "--demo" in args
    persistent = "--serve" in args
    runtime = None
    if not demo_mode:
        # -I excludes cwd from imports; load only modules alongside this trusted script.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from storage_runtime import Runtime, VaultError
        runtime = Runtime(sys.modules[__name__])
        if persistent:
            import threading
            def expire_vault():
                # Network requests can block the main loop. A watchdog still ends
                # the key-owning process at expiry; SQLCipher recovers its journal.
                while True:
                    time.sleep(0.25)
                    if runtime.db and runtime.deadline > 0 and time.monotonic() >= runtime.deadline:
                        os.write(sys.stdout.fileno(), b'{"event":"locked"}\n')
                        os._exit(0)
            threading.Thread(target=expire_vault, daemon=True).start()
    buffer = b""
    try:
        while True:
            if runtime and runtime.db and time.monotonic() >= runtime.deadline:
                runtime.close()
                print(json.dumps({"event": "locked"}), flush=True)
                return
            if persistent:
                if b"\n" not in buffer:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.25)
                    if not ready:
                        continue
                    chunk = os.read(sys.stdin.fileno(), 65536)
                    if not chunk:
                        return
                    buffer += chunk
                    if len(buffer) > 262144:
                        return
                    continue
                raw, buffer = buffer.split(b"\n", 1)
            else:
                raw = sys.stdin.buffer.read(32769)
            data = {}
            try:
                if len(raw) > 32768:
                    raise ClientError("Request is too large.")
                data = json.loads(raw or b"{}")
                if not isinstance(data, dict):
                    data = {}
                    raise ClientError("Request must be an object.")
                result = demo(data) if demo_mode else dispatch(data)
                if runtime and runtime.db and time.monotonic() >= runtime.deadline:
                    runtime.close()
                    raise ClientError("Vault locked. If you were sending, check Sent before trying again.", locked=True)
                response = {"ok": True, **result}
            except ClientError as e:
                response = {"ok": False, "error": str(e), "uncertain": e.uncertain, "locked": e.locked}
            except Exception as e:
                message = str(e) if runtime and isinstance(e, VaultError) else "Cannot complete the request. Check local configuration and file permissions."
                response = {"ok": False, "error": message}
            response["id"] = data.get("id")
            print(json.dumps(response, ensure_ascii=False), flush=True)
            data = {}; raw = b""; result = None; response = None
            if not persistent:
                return
    finally:
        if runtime:
            runtime.close()


if __name__ == "__main__":
    main()
