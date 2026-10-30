"""NVIDIA NIM: reasoning models answer directly, a reply cut off while thinking is a clear error, and the
settings list only shows models that can write."""

from types import SimpleNamespace

from caneta.llm import registry
from caneta.llm.api_providers import NO_THINKING, OpenAICompat


class FakeClient:
    def __init__(self, replies, reject_extra_body=False, models=()):
        self.calls, self.replies, self.reject = [], list(replies), reject_extra_body
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id=m) for m in models]))

    def create(self, **kw):
        self.calls.append(kw)
        if self.reject and "extra_body" in kw:
            raise ValueError("unknown chat_template_kwargs")
        content, finish = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


def _provider(monkeypatch, client, name="nvidia-nim"):
    prov = OpenAICompat(name, "http://nim", "k", "nvidia/nemotron-3.5-lightning-30b-a3b")
    monkeypatch.setattr(prov, "client", lambda timeout=600: client)
    return prov


def test_nim_turns_thinking_off(monkeypatch):
    client = FakeClient([('{"findings": []}', "stop")])
    res = _provider(monkeypatch, client).complete("revise", schema={"type": "object"})
    assert res.ok and res.data == {"findings": []}
    assert client.calls[0]["extra_body"] == NO_THINKING


def test_reply_cut_off_while_thinking_is_explained(monkeypatch):
    client = FakeClient([("", "length")])
    res = _provider(monkeypatch, client).complete("revise", schema={"type": "object"})
    assert not res.ok and "raciocinando" in res.error
    assert len(client.calls) == 1  # no pointless second call without json mode


def test_model_that_rejects_the_switch_is_called_without_it(monkeypatch):
    client = FakeClient([("OK", "stop")], reject_extra_body=True)
    res = _provider(monkeypatch, client).complete("diga OK")
    assert res.ok and res.text == "OK" and "extra_body" not in client.calls[-1]


def test_other_providers_untouched(monkeypatch):
    client = FakeClient([("OK", "stop")])
    _provider(monkeypatch, client, name="ollama").complete("diga OK")
    assert "extra_body" not in client.calls[0]


def test_settings_list_hides_classifiers_and_embedders(monkeypatch):
    client = FakeClient([], models=["nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
                                    "nvidia/nemotron-3.5-embed-1b", "nvidia/nemotron-parse", "nvidia/nemotron-4-340b-reward",
                                    "nvidia/nemotron-3.5-content-safety", "meta/llama-3.3-70b"])
    monkeypatch.setattr(OpenAICompat, "client", lambda self, timeout=600: client)
    assert registry.list_models("nvidia-nim", api_key="k") == ["nvidia/nemotron-3.5-lightning-30b-a3b"]
