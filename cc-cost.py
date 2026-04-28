#!/usr/bin/env python3
"""
cc-cost — quick Claude Code transcript cost analyzer.

Parses Claude Code session transcript JSONL files (from ~/.claude/projects/<dir>/<id>.jsonl)
and reports:
  - per-session cost / token counts
  - prompt-cache hit rate (load-bearing for cost optimization)
  - tool call distribution + per-tool token cost
  - top expensive turns
  - actionable recommendations (--diagnose)

Usage:
  cc-cost                          # scan default ~/.claude/projects
  cc-cost <transcript.jsonl>       # analyze a specific file
  cc-cost --project foo            # scan one project
  cc-cost --json                   # machine-readable output
  cc-cost --diagnose <file>        # detailed cost-optimization advice
  cc-cost --top 10                 # top 10 expensive sessions
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Pricing (USD per 1M tokens) — Claude family as of 2026-04.
# Source: anthropic.com/pricing — Opus tier billed at premium rates.
PRICES = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.5},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.5},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}
DEFAULT_PRICE_KEY = "claude-opus-4-7"


def price_for(model: str) -> dict[str, float]:
    if not model:
        return PRICES[DEFAULT_PRICE_KEY]
    for key, p in PRICES.items():
        if key in model:
            return p
    return PRICES[DEFAULT_PRICE_KEY]


@dataclass
class Stats:
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_create_tokens: int = 0
    cache_read_tokens: int = 0
    cost: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    top_turns: list[tuple[float, str, int]] = field(default_factory=list)
    model: str = DEFAULT_PRICE_KEY
    output_per_turn: list[int] = field(default_factory=list)
    cache_write_total: int = 0


def analyze_file(path: Path) -> Stats:
    s = Stats()
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.get("type")
            msg = rec.get("message") or {}
            if kind != "assistant" or not isinstance(msg, dict):
                continue

            usage = msg.get("usage") or {}
            if usage:
                s.turns += 1
                inp = int(usage.get("input_tokens", 0))
                out = int(usage.get("output_tokens", 0))
                cw = int(usage.get("cache_creation_input_tokens", 0))
                cr = int(usage.get("cache_read_input_tokens", 0))
                s.input_tokens += inp
                s.output_tokens += out
                s.cache_create_tokens += cw
                s.cache_read_tokens += cr
                s.output_per_turn.append(out)
                if msg.get("model"):
                    s.model = msg["model"]
                p = price_for(s.model)
                turn_cost = (
                    inp * p["input"]
                    + out * p["output"]
                    + cw * p["cache_write"]
                    + cr * p["cache_read"]
                ) / 1_000_000
                s.cost += turn_cost
                if turn_cost >= 0.01:
                    snippet = json.dumps(msg.get("content", ""))[:80]
                    s.top_turns.append((turn_cost, snippet, out))

            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        s.tool_calls[block.get("name", "?")] += 1

    s.top_turns.sort(reverse=True)
    s.top_turns = s.top_turns[:10]
    return s


def cache_hit_rate(s: Stats) -> float:
    denom = s.cache_read_tokens + s.cache_create_tokens + s.input_tokens
    if denom == 0:
        return 0.0
    return s.cache_read_tokens / denom


def diagnose(s: Stats) -> list[str]:
    """Return human-readable, prioritized recommendations."""
    recs: list[str] = []
    chr_pct = cache_hit_rate(s) * 100

    if chr_pct < 30 and s.turns > 5:
        recs.append(
            f"Cache hit rate is {chr_pct:.1f}% (target: 80%+). "
            f"You are paying ~10× more than necessary on prompt input. "
            f"Move long static context (system prompt, tool definitions, large files) "
            f"to the START of each request and mark it cache_control."
        )
    elif chr_pct < 60 and s.turns > 5:
        recs.append(
            f"Cache hit rate is {chr_pct:.1f}% (target: 80%+). "
            f"Some context is hot, but you're still re-reading non-cached chunks. "
            f"Audit: which messages change between turns? Pull volatile content "
            f"out of the cached prefix."
        )

    if s.output_per_turn:
        avg_out = sum(s.output_per_turn) / len(s.output_per_turn)
        if avg_out > 1500:
            recs.append(
                f"Average output is {avg_out:.0f} tokens/turn. "
                f"Output costs 5× input, so verbose responses dominate. "
                f"Add to system prompt: 'Keep responses concise. Skip preambles and summaries.'"
            )

    if s.tool_calls:
        total_calls = sum(s.tool_calls.values())
        bash_calls = s.tool_calls.get("Bash", 0)
        if total_calls > 0 and bash_calls / total_calls > 0.6 and bash_calls > 20:
            recs.append(
                f"Bash dominates ({bash_calls}/{total_calls} calls). "
                f"Each Bash call re-pays the prompt — group commands with && or ; "
                f"into one Bash call where practical, and pipe long output to a "
                f"file then Read the relevant part."
            )
        read_calls = s.tool_calls.get("Read", 0)
        if read_calls > 30:
            recs.append(
                f"{read_calls} Read calls in one session. "
                f"If you're re-reading the same files, bundle context into a single "
                f"upfront file or use a Skill that records relevant parts to a memory file."
            )
        if total_calls > 0 and len(s.tool_calls) == 1:
            sole_tool = next(iter(s.tool_calls))
            recs.append(
                f"All {total_calls} tool calls were `{sole_tool}`. "
                f"Diversifying tool use (Read, Edit, Grep, etc.) often produces "
                f"more efficient turns than repeated Bash invocations."
            )

    if s.top_turns:
        cost_top1 = s.top_turns[0][0]
        if cost_top1 > 0.5 and s.turns > 0 and cost_top1 / s.cost > 0.2:
            recs.append(
                f"One turn cost ${cost_top1:.2f} ({cost_top1/s.cost*100:.0f}% of total). "
                f"That's a single LLM round-trip. Investigate the top-expensive-turns "
                f"output above — usually one massive tool result fed back into context."
            )

    if not recs:
        recs.append("No obvious inefficiencies. This session looks well-optimized.")

    return recs


def report_text(label: str, s: Stats, show_diag: bool = False) -> str:
    chr_pct = cache_hit_rate(s) * 100
    parts = [f"\n=== {label} ==="]
    parts.append(f"  model:           {s.model}")
    parts.append(f"  turns:           {s.turns}")
    parts.append(f"  input tokens:    {s.input_tokens:>12,}")
    parts.append(f"  output tokens:   {s.output_tokens:>12,}")
    parts.append(f"  cache write:     {s.cache_create_tokens:>12,}")
    parts.append(f"  cache read:      {s.cache_read_tokens:>12,}")
    parts.append(f"  cache hit rate:  {chr_pct:>11.1f}%   (higher = cheaper)")
    parts.append(f"  total cost USD:  ${s.cost:>11.4f}")
    if s.tool_calls:
        parts.append("  tool calls:")
        for tool, n in sorted(s.tool_calls.items(), key=lambda x: -x[1]):
            parts.append(f"    {tool:30s} {n}")
    if s.top_turns:
        parts.append("  top 5 expensive turns (cost / output_tokens):")
        for cost, snip, out_t in s.top_turns[:5]:
            parts.append(f"    ${cost:7.4f}  out={out_t:>5}t  {snip}")
    if show_diag:
        parts.append("\n  Recommendations:")
        for i, r in enumerate(diagnose(s), 1):
            parts.append(f"    {i}. {r}")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(prog="cc-cost", description="Claude Code transcript cost analyzer")
    ap.add_argument("path", nargs="?", help="transcript.jsonl, or omitted to scan ~/.claude/projects")
    ap.add_argument("--project", help="scan only this project dir under ~/.claude/projects")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--diagnose", action="store_true", help="print specific cost-optimization recommendations")
    ap.add_argument("--top", type=int, default=0, help="show only top N most expensive sessions")
    args = ap.parse_args()

    files: list[Path] = []
    if args.path:
        p = Path(args.path)
        if not p.exists():
            print(f"not found: {p}", file=sys.stderr)
            return 1
        files = [p] if p.is_file() else sorted(p.glob("**/*.jsonl"))
    else:
        root = Path.home() / ".claude" / "projects"
        if args.project:
            root = root / args.project
        if not root.exists():
            print(f"no transcripts under {root}", file=sys.stderr)
            return 1
        files = sorted(root.glob("**/*.jsonl"))

    if not files:
        print("no transcripts found", file=sys.stderr)
        return 1

    total = Stats()
    per_file: list[tuple[str, Stats]] = []
    for f in files:
        s = analyze_file(f)
        if s.turns == 0:
            continue
        per_file.append((str(f), s))
        total.turns += s.turns
        total.input_tokens += s.input_tokens
        total.output_tokens += s.output_tokens
        total.cache_create_tokens += s.cache_create_tokens
        total.cache_read_tokens += s.cache_read_tokens
        total.cost += s.cost
        total.output_per_turn.extend(s.output_per_turn)
        for k, v in s.tool_calls.items():
            total.tool_calls[k] += v

    if args.top:
        per_file.sort(key=lambda kv: -kv[1].cost)
        per_file = per_file[: args.top]

    if args.json:
        out = {
            "files": [{"path": p, "turns": s.turns, "cost_usd": round(s.cost, 4),
                       "input": s.input_tokens, "output": s.output_tokens,
                       "cache_read": s.cache_read_tokens, "cache_write": s.cache_create_tokens,
                       "cache_hit_rate": round(cache_hit_rate(s), 4),
                       "model": s.model, "tool_calls": dict(s.tool_calls),
                       "recommendations": diagnose(s) if args.diagnose else None}
                      for p, s in per_file],
            "total": {"turns": total.turns, "cost_usd": round(total.cost, 4),
                      "input": total.input_tokens, "output": total.output_tokens,
                      "cache_read": total.cache_read_tokens, "cache_write": total.cache_create_tokens,
                      "cache_hit_rate": round(cache_hit_rate(total), 4),
                      "tool_calls": dict(total.tool_calls)},
        }
        print(json.dumps(out, indent=2))
        return 0

    for path, s in per_file:
        print(report_text(Path(path).name, s, show_diag=args.diagnose))
    if len(per_file) > 1:
        print(report_text(f"TOTAL across {len(per_file)} session(s)", total, show_diag=args.diagnose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
