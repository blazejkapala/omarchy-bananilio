import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import client


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for name, value in (("CONFIG", base / "config"), ("STATE", base / "state")):
            p = patch.object(client, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.account = client.validate_account({"name": "Test", "sid": "AC" + "0" * 32, "token": "1" * 32})
        client.write_json(client.CONFIG / "accounts.json", {"accounts": [self.account]})
        self.request = {"action": "send", "account": self.account["id"], "from": "+12025550101",
                        "to": "+12025550120", "body": "Synthetic test message", "request_id": "test-request-00000001"}

    def test_demo_never_reads_storage_or_network(self):
        with patch.object(client, "read_json", side_effect=AssertionError("storage")), \
             patch.object(client, "write_json", side_effect=AssertionError("storage")), \
             patch.object(client, "api", side_effect=AssertionError("network")):
            for action in ("snapshot", "send", "add_account", "mark_read"):
                self.assertTrue(client.demo({"action": action})["demo"])

    def test_private_credentials(self):
        self.assertEqual((client.CONFIG / "accounts.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(client.CONFIG.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("token", client.public_account(self.account))
        self.assertNotIn("sid", client.public_account(self.account))

    def test_send_is_not_repeated(self):
        with patch.object(client, "numbers", return_value=[{"number": self.request["from"]}]), \
             patch.object(client, "api", return_value={"sid": "SM-test", "status": "queued"}) as api:
            self.assertEqual(client.send(self.request), client.send(self.request))
            api.assert_called_once()
            with self.assertRaises(client.ClientError):
                client.send({**self.request, "body": "Changed content"})
            api.assert_called_once()

    def test_unknown_send_blocks_retry(self):
        with patch.object(client, "numbers", return_value=[{"number": self.request["from"]}]), \
             patch.object(client, "api", side_effect=client.ClientError("Timeout", uncertain=True)) as api:
            for _ in range(2):
                with self.assertRaises(client.ClientError) as err:
                    client.send(self.request)
                self.assertTrue(err.exception.uncertain)
            api.assert_called_once()

    def test_unowned_sender_never_posts(self):
        with patch.object(client, "numbers", return_value=[]), patch.object(client, "api") as api:
            with self.assertRaises(client.ClientError):
                client.send(self.request)
            api.assert_not_called()

    def test_unread_baseline_and_mark_read(self):
        def account(keys):
            return {"id": "test", "messages": [{"key": k, "inbound": True} for k in keys]}
        self.assertEqual(client.update_unread([account(["old"])]), (0, 0))
        self.assertEqual(client.update_unread([account(["new", "old"])]), (1, 1))
        self.assertEqual(client.update_unread([account(["new", "old"])]), (0, 1))
        client.dispatch({"action": "mark_read", "keys": ["new"]})
        self.assertEqual(client.update_unread([account(["new", "old"])]), (0, 0))

    def test_symlink_private_file_is_rejected(self):
        target = client.CONFIG / "accounts.json"
        link = client.CONFIG / "link.json"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            client.read_json(link, {})

    def test_visibility_persists_and_new_numbers_stay_hidden(self):
        found = [{"number": "+12025550101"}, {"number": "+12025550102"}]
        aid = self.account["id"]
        with patch.object(client, "numbers", return_value=found):
            client.dispatch({"action": "set_visible_numbers", "account": aid, "numbers": ["+12025550101"]})
            self.assertEqual(client.visible_numbers(aid, found + [{"number": "+12025550103"}]), {"+12025550101"})
            with self.assertRaises(client.ClientError):
                client.dispatch({"action": "set_visible_numbers", "account": aid, "numbers": ["+12025550199"]})
            client.dispatch({"action": "set_visible_numbers", "account": aid, "numbers": []})
            self.assertEqual(client.visible_numbers(aid, found), set())

    def test_hidden_messages_do_not_notify_or_appear(self):
        aid = self.account["id"]
        client.write_json(client.CONFIG / "visibility.json", {aid: ["+12025550101"]})
        def fake_fetch(*args):
            return {"id": aid, "numbers": [{"number": "+12025550101"}, {"number": "+12025550102"}],
                    "messages": [{"key": "hidden", "inbound": True, "to": "+12025550102"}], "error": ""}
        client.update_unread([{"id": aid, "messages": []}])
        with patch.object(client, "fetch_account", side_effect=fake_fetch):
            result = client.snapshot({})
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["unread"], 0)
        self.assertEqual(result["accounts"][0]["messages"], [])
        self.assertFalse(result["accounts"][0]["numbers"][1]["visible"])

    def test_hidden_number_cannot_send(self):
        client.write_json(client.CONFIG / "visibility.json", {self.account["id"]: []})
        with patch.object(client, "numbers", return_value=[{"number": self.request["from"]}]), patch.object(client, "api") as api:
            with self.assertRaises(client.ClientError):
                client.send(self.request)
            api.assert_not_called()

    def enable_incognito(self):
        return client.dispatch({"action": "configure_security", "enabled": True, "password": "test-only-passphrase", "timeout": 5})

    def test_incognito_blocks_all_private_actions(self):
        self.enable_incognito()
        with patch.object(client, "api") as api:
            for action in ("snapshot", "send", "add_account", "disconnect_account", "set_visible_numbers", "mark_read", "save_contact", "delete_contact", "share_contacts", "import_contacts", "set_number_label"):
                with self.assertRaises(client.ClientError) as err:
                    client.dispatch({"action": action})
                self.assertTrue(err.exception.locked)
            api.assert_not_called()
        self.assertNotIn("test-only-passphrase", (client.CONFIG / "incognito.json").read_text())

    def test_incognito_expires_without_poll_renewal(self):
        with patch.object(client.time, "time", return_value=100):
            token = self.enable_incognito()["session"]
        with patch.object(client.time, "time", return_value=104), patch.object(client, "snapshot", return_value={"accounts": []}):
            client.dispatch({"action": "snapshot", "session": token})
        with patch.object(client.time, "time", return_value=106):
            with self.assertRaises(client.ClientError):
                client.dispatch({"action": "snapshot", "session": token})
            unlocked = client.dispatch({"action": "unlock", "password": "test-only-passphrase"})
            self.assertNotEqual(unlocked["session"], token)

    def test_explicit_activity_renews_session(self):
        with patch.object(client.time, "time", return_value=100):
            token = self.enable_incognito()["session"]
        with patch.object(client.time, "time", return_value=104):
            client.dispatch({"action": "touch_session", "session": token})
        with patch.object(client.time, "time", return_value=108):
            client.require_session({"session": token})
        with patch.object(client.time, "time", return_value=110):
            with self.assertRaises(client.ClientError):
                client.require_session({"session": token})

    def test_wrong_password_is_rate_limited_and_cannot_disable(self):
        token = self.enable_incognito()["session"]
        with self.assertRaises(client.ClientError):
            client.dispatch({"action": "configure_security", "session": token, "enabled": False, "current_password": "wrong"})
        self.assertTrue(client.security_config()["enabled"])
        with self.assertRaisesRegex(client.ClientError, "wait"):
            client.dispatch({"action": "unlock", "password": "test-only-passphrase"})

    def test_disable_needs_current_password(self):
        token = self.enable_incognito()["session"]
        result = client.dispatch({"action": "configure_security", "session": token, "enabled": False, "current_password": "test-only-passphrase"})
        self.assertFalse(result["enabled"])
        client.require_session({})

    def test_disconnect_is_local_and_removes_credentials(self):
        aid = self.account["id"]
        client.write_json(STATE_PATH := client.STATE / ("numbers-" + aid + ".json"), {"numbers": []})
        with patch.object(client, "api") as api:
            client.dispatch({"action": "disconnect_account", "account": aid})
            api.assert_not_called()
        self.assertEqual(client.accounts(), [])
        self.assertFalse(STATE_PATH.exists())
        self.assertNotIn(self.account["token"], (client.CONFIG / "accounts.json").read_text())

    def test_separate_and_shared_contacts(self):
        aid = self.account["id"]
        first, second = "+12025550101", "+12025550102"
        base = {"account": aid, "from": first}
        with patch.object(client, "numbers", return_value=[{"number": first}, {"number": second}]):
            client.dispatch({**base, "action": "save_contact", "name": "Alex", "number": "+12025550120"})
            self.assertEqual(client.contact_list(client.contacts_store(), aid, second), [])
            client.dispatch({**base, "action": "share_contacts", "shared": True})
            self.assertEqual(len(client.contact_list(client.contacts_store(), aid, second)), 1)
            client.dispatch({**base, "from": second, "action": "save_contact", "original": "+12025550120", "name": "Alex edited", "number": "+12025550121"})
            self.assertEqual(client.contact_list(client.contacts_store(), aid, first)[0]["name"], "Alex edited")
            client.dispatch({**base, "action": "share_contacts", "shared": False})
            self.assertEqual(client.contact_list(client.contacts_store(), aid, first)[0]["name"], "Alex")
            self.assertEqual(client.contact_list(client.contacts_store(), aid, second), [])
            client.dispatch({**base, "action": "delete_contact", "number": "+12025550120"})
            self.assertEqual(client.contact_list(client.contacts_store(), aid, first), [])

    def test_import_copies_to_separate_books_without_overwrite(self):
        aid = self.account["id"]
        first, second = "+12025550101", "+12025550102"
        source = {"+12025550120": {"name": "Synthetic Alex"}, "DEMO-SERVICE": {"name": "Synthetic service"}}
        client.write_json(client.CONFIG / "contacts-import.json", source)
        with patch.object(client, "numbers", return_value=[{"number": first}, {"number": second}]):
            client.dispatch({"action": "save_contact", "account": aid, "from": first, "name": "Existing Alex", "number": "+12025550120"})
            result = client.dispatch({"action": "import_contacts"})
        self.assertEqual(result["books"], 2)
        self.assertEqual(result["added_entries"], 3)
        store = client.contacts_store()
        self.assertEqual(store["books"][aid + ":" + first]["+12025550120"]["name"], "Existing Alex")
        self.assertEqual(store["books"][aid + ":" + second]["+12025550120"]["name"], "Synthetic Alex")
        self.assertFalse((client.CONFIG / "contacts-import.json").exists())

    def test_contact_duplicates_and_labels_are_local(self):
        aid, number = self.account["id"], "+12025550101"
        data = {"action": "save_contact", "account": aid, "from": number, "name": "Demo", "number": "+12025550120"}
        with patch.object(client, "numbers", return_value=[{"number": number}]), patch.object(client, "api") as api:
            client.dispatch(data)
            with self.assertRaises(client.ClientError):
                client.dispatch(data)
            client.dispatch({"action": "set_number_label", "account": aid, "number": number, "label": "Work"})
            api.assert_not_called()
        self.assertEqual(client.read_json(client.CONFIG / "number-labels.json", {})[aid + ":" + number], "Work")


if __name__ == "__main__":
    unittest.main()
