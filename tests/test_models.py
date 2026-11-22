import json

import requests

from laptop_ai import models


class FakeResp:
    def __init__(self, lines, status=200):
        self.lines, self.status_code = lines, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        return iter(json.dumps(x).encode() if isinstance(x, dict) else x for x in self.lines)


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.sent = resp, exc, []

    def post(self, url, **kw):
        self.sent.append((url, kw["json"]))
        if self.exc:
            raise self.exc
        return self.resp


def test_pull_reports_progress_and_finishes():
    got = []
    sess = FakeSession(FakeResp([{"status": "pulling manifest"}, {"status": "pulling abc", "total": 200, "completed": 50}, b"",
                                 b"not json", {"status": "pulling abc", "total": 200, "completed": 200}, {"status": "success"}]))
    ok, msg = models.pull("http://x:1/", "llama3.2:3b", lambda f, s: got.append((f, s)), session=sess)
    assert ok and "ready" in msg and sess.sent == [("http://x:1/api/pull", {"model": "llama3.2:3b", "stream": True})]
    assert (0.25, "pulling abc") in got and (1.0, "pulling abc") in got and got[0][0] == -1.0


def test_pull_failures_are_explained():
    assert models.pull("http://x", "m", session=FakeSession(FakeResp([{"error": "pull model manifest: file does not exist"}])))[1].startswith("pull model")
    assert "isn't running" in models.pull("http://x", "m", session=FakeSession(exc=requests.ConnectionError()))[1]
    assert "resumes" in models.pull("http://x", "m", session=FakeSession(exc=requests.ReadTimeout()))[1]
    assert "refused" in models.pull("http://x", "m", session=FakeSession(FakeResp([], 404)))[1]
    assert "stopped" in models.pull("http://x", "m", cancelled=lambda: True, session=FakeSession(FakeResp([{"status": "x"}])))[1]
    assert not models.pull("http://x", "m", session=FakeSession(FakeResp([{"status": "pulling"}])))[0]


def test_has_handles_latest_tags():
    assert models.has("llama3.2", ["llama3.2:latest"]) and models.has("llama3.2:3b", ["llama3.2:3b"])
    assert not models.has("llama3.2:3b", ["llama3.2:1b"])
