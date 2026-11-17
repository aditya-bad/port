"""Draft with Claude, with a stand-in for the API: the first answer has a
broken script, the check finds it, the second answer fixes it."""
import json
import time
from types import SimpleNamespace

import pytest

from tests.conftest import can_render

pytestmark = pytest.mark.skipif(not can_render(), reason="needs Node and a browser for the check")


class FakeStream:
    def __init__(self, calls, answer):
        self.answer = answer
        calls.append(None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.answer))],
            usage=SimpleNamespace(input_tokens=12000, output_tokens=3000, cache_creation_input_tokens=0, cache_read_input_tokens=0))


def test_claude_drafts_then_fixes_what_the_check_finds(client, monkeypatch):
    import anthropic
    from app import config, store
    calls, seen = [], []
    good = store.example_script()
    answers = [{"scenes_js": "scene({ title: 'x', dur: 5, steps: [[1, 'starts late']] }, p => () => {});",
                "description": "d", "say": [], "notes": ""},
               {"scenes_js": good, "description": "Two short scenes.", "say": [["P&L", "P and L"]], "notes": "All from the notes."}]

    class FakeClient:
        def __init__(self, api_key=None):
            assert api_key == "sk-test"
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

        def stream(self, **kw):
            seen.append(kw)
            return FakeStream(calls, answers[len(calls)])
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)
    client.put("/api/settings", json={"anthropic_api_key": "sk-test", "rounds": 2})
    pid = client.post("/api/projects", json={"title": "Demo", "brief": "explain it"}).json()["id"]
    client.post(f"/api/projects/{pid}/uploads", files=[("files", ("notes.md", b"# The notes", "text/markdown"))])
    jid = client.post(f"/api/projects/{pid}/jobs", json={"kind": "draft"}).json()["id"]
    for _ in range(300):
        v = client.get(f"/api/jobs/{jid}").json()
        if v["state"] not in ("queued", "running"):
            break
        time.sleep(0.5)
    assert v["state"] == "done", (v["error"], v["log"][-15:])
    assert len(calls) == 2, "one draft, one fix"
    first, second = seen
    assert first["model"] == "claude-opus-5-5" and first["output_config"]["format"]["type"] == "json_schema"
    assert "# The notes" in json.dumps(first["messages"], default=str) and "explain it" in json.dumps(first["messages"], default=str)
    assert "first step starts at 0" in json.dumps(second["messages"][-1], default=str), "the check's finding goes back to Claude"
    assert store.script(pid) == good
    p = client.get(f"/api/projects/{pid}").json()
    assert p["description"] == "Two short scenes." and p["settings"]["say"] == [["P&L", "P and L"]]
    assert v["result"]["cost_usd"] == round((24000 * 4 + 6000 * 20) / 1e6, 3)
