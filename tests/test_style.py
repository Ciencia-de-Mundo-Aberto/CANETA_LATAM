from caneta import config, style
from caneta.review.anchoring import screen

TEXT = "Os hidroides são organismos coloniais. Eles vive em substratos artificiais do litoral."


def test_languagetool_matches_become_anchored_findings():
    off = TEXT.index("vive")
    matches = [{"message": "Concordância verbal", "offset": off, "length": 4, "replacements": [{"value": "vivem"}],
                "rule": {"category": {"id": "GRAMMAR"}}}]
    findings = style.to_findings(TEXT, matches)
    kept, discarded = screen(findings, TEXT, "gramatica", strict_quote=False)
    assert not discarded and kept[0]["edit"]["replace"].count("vivem") == 1
    assert kept[0]["rotulo"] == "Imprecisão"


def test_languagetool_off_by_default_sends_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(style.httpx, "post", lambda *a, **k: called.append(1))
    assert config.load().languagetool == "off"
    assert style.check(TEXT) == [] and not called
