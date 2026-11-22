"""'open chrome' without the AI, and small models that print a tool call as text."""
import json
import os

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

from laptop_ai import brain, config, tools  # noqa: E402


class FakeExecutor:
    def __init__(self, result=None):
        self.calls, self.result, self.override = [], result or {"status": "ok"}, None

    def run(self, name, args, source="user"):
        self.calls.append((name, args, source))
        return json.dumps(self.result)

    def available(self):
        return list(tools.REGISTRY.values())


def test_open_known_apps_skips_the_ai():
    for text, app in [("open Chrome", "chrome"), ("Open the task manager.", "task manager"),
                      ("please launch notepad!", "notepad"), ("start vs code", "vs code")]:
        ex, seen = FakeExecutor(), []
        assert brain.quick_open(text, ex, lambda n, a: seen.append(n)) == f"Opened {app}."
        assert ex.calls == [("open_app", {"name": app}, "user")] and seen == ["open_app"]


def test_unknown_or_other_requests_are_left_to_the_ai():
    ex = FakeExecutor()
    for text in ("open my bank account", "how is my battery", "what should I open", "open", ""):
        assert brain.quick_open(text, ex, lambda n, a: None) is None
    assert ex.calls == []


def test_quick_open_reports_refusals_honestly():
    ex = FakeExecutor({"status": "error", "message": "You declined, so I did not do it."})
    assert brain.quick_open("open chrome", ex, lambda n, a: None) == "You declined, so I did not do it."


def test_brain_uses_the_shortcut_and_remembers_it():
    ex = FakeExecutor()
    b = brain.Brain(config.Settings(), ex)
    assert b.ask("open edge") == "Opened edge." and b.last_backend == "built-in shortcut"
    assert b.history[-1]["content"] == "Opened edge."


def test_a_tool_call_printed_as_text_is_recognised():
    known = {"get_system_status", "open_app"}
    r = brain.text_tool_call('{"name": "open_app", "parameters": {"name": "notepad"}}', known)
    assert r == {"name": "open_app", "arguments": {"name": "notepad"}}
    cut = brain.text_tool_call('{"name": "get_system_status", "parameters": {"}}', known)      # cut off, as seen
    assert cut == {"name": "get_system_status", "arguments": {}}
    assert brain.text_tool_call('{"name": "rm_everything", "parameters": {}}', known) is None   # not a real tool
    assert brain.text_tool_call("Your battery is 80%.", known) is None
    assert brain.text_tool_call('{"note": "hello"}', known) is None


def test_ollama_runs_a_rescued_tool_call_instead_of_showing_json():
    ex = FakeExecutor({"status": "ok", "battery": "80%"})
    replies = iter([{"message": {"content": '{"name": "get_system_status", "parameters": {}}'}},
                    {"message": {"content": "Battery is 80%."}}])

    class R:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data
    backend = brain.OllamaBackend(config.Settings(), post=lambda *a, **k: R(next(replies)))
    out = backend.run([{"role": "user", "content": "battery?"}], "sys", ex.available(), ex, lambda n, a: None)
    assert out == "Battery is 80%." and ex.calls[0][0] == "get_system_status"
