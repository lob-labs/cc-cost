"""Tests for cc-cost. Run: python3 -m pytest test_cc_cost.py -v"""

import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, '/tmp/lob-cc-cost')
import cc_cost  # noqa


def test_price_for_known_model():
    p = cc_cost.price_for("claude-opus-4-7")
    assert p["input"] == 15.0
    assert p["output"] == 75.0
    assert p["cache_read"] == 1.5


def test_price_for_unknown_falls_back_to_default():
    p = cc_cost.price_for("claude-future-99")
    assert p == cc_cost.PRICES[cc_cost.DEFAULT_PRICE_KEY]


def test_price_for_empty_string():
    p = cc_cost.price_for("")
    assert p == cc_cost.PRICES[cc_cost.DEFAULT_PRICE_KEY]


def test_price_substring_match():
    # claude-opus-4-7-1m should still match opus pricing
    p = cc_cost.price_for("claude-opus-4-7-1m")
    assert p["input"] == 15.0


def test_cache_hit_rate_zero_when_no_data():
    s = cc_cost.Stats()
    assert cc_cost.cache_hit_rate(s) == 0.0


def test_cache_hit_rate_calculation():
    s = cc_cost.Stats(input_tokens=100, cache_create_tokens=200, cache_read_tokens=700)
    assert abs(cc_cost.cache_hit_rate(s) - 0.7) < 1e-9


def test_analyze_simple_transcript():
    rec = {
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 5000,
            },
            "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(rec) + "\n")
        path = Path(f.name)
    s = cc_cost.analyze_file(path)
    assert s.turns == 1
    assert s.input_tokens == 100
    assert s.output_tokens == 200
    assert s.tool_calls["Bash"] == 1
    assert s.model == "claude-sonnet-4-6"
    # Cost: (100*3.0 + 200*15 + 1000*3.75 + 5000*0.3) / 1M
    expected = (100 * 3.0 + 200 * 15.0 + 1000 * 3.75 + 5000 * 0.3) / 1_000_000
    assert abs(s.cost - expected) < 1e-9


def test_analyze_skips_non_assistant_lines():
    recs = [
        {"type": "user", "message": "ignored"},
        {"type": "assistant", "message": {"model": "claude-opus-4-7", "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        path = Path(f.name)
    s = cc_cost.analyze_file(path)
    assert s.turns == 1


def test_analyze_skips_malformed_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("not valid json\n")
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-7", "usage": {"input_tokens": 1}}}) + "\n")
        path = Path(f.name)
    s = cc_cost.analyze_file(path)
    assert s.turns == 1


def test_diagnose_recommends_low_cache_hit():
    s = cc_cost.Stats(turns=10, input_tokens=10000, cache_read_tokens=1000)
    recs = cc_cost.diagnose(s)
    assert any("Cache hit rate" in r for r in recs)


def test_diagnose_well_optimized_session():
    s = cc_cost.Stats(turns=5, input_tokens=100, cache_read_tokens=50000, output_per_turn=[200] * 5)
    recs = cc_cost.diagnose(s)
    assert any("well-optimized" in r for r in recs)


def test_report_text_includes_key_metrics():
    s = cc_cost.Stats(turns=3, cost=1.234, input_tokens=100)
    txt = cc_cost.report_text("test", s)
    assert "test" in txt
    assert "$    1.2340" in txt or "$1.234" in txt or "1.234" in txt


def test_report_markdown_format():
    s = cc_cost.Stats(turns=3, cost=1.234)
    md = cc_cost.report_markdown("test", s)
    assert md.startswith("###")
    assert "| field | value |" in md


if __name__ == "__main__":
    import unittest
    # Run as basic asserts instead of pytest to avoid dep
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append((t.__name__, e))
    print(f"\n{passed}/{len(tests)} tests passed")
    for name, err in failed:
        print(f"  FAILED: {name}: {err}")
