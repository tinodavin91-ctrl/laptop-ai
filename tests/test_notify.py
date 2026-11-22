"""Telegram tests with a fake Telegram server (no internet, no real bot)."""
import os

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest   # noqa: E402
import requests  # noqa: E402

from laptop_ai import config, notify  # noqa: E402

TOKEN = "123456789:" + "A" * 35


class FakeTelegram:
    def __init__(self):
        self.calls, self.updates, self.fail = [], [], None

    def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        if self.fail:
            raise self.fail
        if "badtoken" in url:
            return Resp({"ok": False, "description": "Unauthorized"}, 401)
        if method == "getMe":
            return Resp({"ok": True, "result": {"username": "my_laptop_bot"}})
        if method == "getUpdates":
            offset = (json or {}).get("offset")
            ups = [u for u in self.updates if offset is None or offset < 0 or u["update_id"] >= offset]
            if offset == -1:
                ups = self.updates[-1:]
            return Resp({"ok": True, "result": ups})
        return Resp({"ok": True, "result": {"message_id": 1}})


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


def msg(update_id, chat_id, text, kind="private"):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": kind}, "text": text}}


def make(chat="42"):
    s = config.Settings()
    s.telegram_chat_id = chat
    fake = FakeTelegram()
    return notify.Telegram(s, fake.post, lambda: TOKEN), fake


def test_token_shape():
    assert notify.looks_like_token(TOKEN) and not notify.looks_like_token("hello")
    assert not notify.save_token("hello")[0]


def test_token_storage_uses_keyring_not_settings(monkeypatch, tmp_path):
    store = {}

    class FakeKeyring:
        def set_password(self, svc, acct, pw): store[(svc, acct)] = pw
        def get_password(self, svc, acct): return store.get((svc, acct))
        def delete_password(self, svc, acct): store.pop((svc, acct), None)
    monkeypatch.setattr(notify, "keyring", FakeKeyring())
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert notify.save_token(TOKEN) == (True, "")
    assert notify.get_token() == TOKEN
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    config.Settings().save()
    assert TOKEN not in (tmp_path / "s.json").read_text()          # never written to settings.json
    notify.forget_token()
    assert notify.get_token() == ""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    assert notify.get_token() == "env-token"


def test_no_keyring_gives_helpful_message(monkeypatch):
    monkeypatch.setattr(notify, "keyring", None)
    ok, err = notify.save_token(TOKEN)
    assert not ok and "keyring" in err


def test_bot_name_and_send():
    tg, fake = make()
    assert tg.bot_name() == (True, "@my_laptop_bot")
    assert tg.send("hello") == (True, "")
    method, payload = fake.calls[-1]
    assert method == "sendMessage" and payload["chat_id"] == "42" and payload["text"] == "hello"
    tg.send("x" * 9000)
    assert len(fake.calls[-1][1]["text"]) == notify.MAX_TEXT


def test_send_failures_are_reported_not_raised():
    tg, fake = make()
    fake.fail = requests.ConnectionError()
    ok, err = tg.send("hi")
    assert not ok and "internet" in err
    tg2, _ = make(chat="")
    assert tg2.send("hi") == (False, "No chat is linked yet.")
    bad = notify.Telegram(config.Settings(), FakeTelegram().post, lambda: "badtoken")
    bad.s.telegram_chat_id = "1"
    ok, err = bad.send("hi")
    assert not ok and "rejected" in err


def test_find_chat_picks_private_chat_only():
    tg, fake = make(chat="")
    assert tg.find_chat()[0] is False
    fake.updates = [msg(1, -100, "group talk", "group"), msg(2, 777, "hi")]
    assert tg.find_chat() == (True, "777")


def test_questions_only_from_the_linked_chat_and_old_messages_skipped():
    tg, fake = make(chat="42")
    asked = []
    pq = notify.PhoneQuestions(tg, lambda t: asked.append(t) or f"answer to {t}")
    fake.updates = [msg(10, 42, "old message sent while the app was closed")]
    pq._skip_old_messages()
    assert pq.offset == 11
    fake.updates += [msg(11, 999, "close chrome please"),                  # a stranger
                     msg(12, 42, "how is my battery?"),
                     msg(13, -5, "group msg", "group")]
    assert pq.poll_once() == 1
    assert asked == ["how is my battery?"]
    sent = [c for c in fake.calls if c[0] == "sendMessage"]
    assert len(sent) == 1 and sent[0][1]["text"] == "answer to how is my battery?"
    assert all(c[1]["chat_id"] == "42" for c in sent)                     # strangers never get a reply
    assert pq.poll_once() == 0 and len(asked) == 1                        # nothing handled twice


def test_handler_error_becomes_a_reply():
    tg, fake = make()

    def boom(_):
        raise RuntimeError("bad")
    pq = notify.PhoneQuestions(tg, boom)
    fake.updates = [msg(1, 42, "hi there")]
    assert pq.poll_once() == 1
    assert "Something went wrong" in fake.calls[-1][1]["text"]


def test_help_command_does_not_reach_the_ai():
    tg, fake = make()
    called = []
    pq = notify.PhoneQuestions(tg, lambda t: called.append(t) or "x")
    fake.updates = [msg(1, 42, "/start")]
    pq.poll_once()
    assert not called and "only look" in fake.calls[-1][1]["text"]


def test_offline_poll_returns_minus_one():
    tg, fake = make()
    fake.fail = requests.ConnectionError()
    assert notify.PhoneQuestions(tg, lambda t: "x").poll_once() == -1


@pytest.mark.parametrize("text", ["close chrome", "delete everything"])
def test_phone_text_cannot_bypass_read_only(text):
    """The handler used by the app runs the executor in read-only mode (see test_laptop_ai)."""
    from laptop_ai import tools
    s = config.Settings()
    ex = tools.Executor(s, lambda t: True)
    ex.override = "read_only"
    assert {t.risk for t in ex.available()} == {tools.READ}
