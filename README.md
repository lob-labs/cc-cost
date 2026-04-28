# cc-cost

A tiny Claude Code cost analyzer. Parses Claude Code session transcript JSONL files
and reports per-session cost, prompt-cache hit rate, tool-call distribution, and the
top expensive turns.

Knowing your cache hit rate is load-bearing for cost — a session at 91% hit rate
costs ~10× less than the same work at 0%.

## Install

```bash
pip install cc-cost
```

Or no-install:

```bash
curl -O https://raw.githubusercontent.com/lob-labs/cc-cost/main/cc-cost.py
chmod +x cc-cost.py
```

No dependencies beyond Python 3.9+.

## Use

```bash
# Scan all sessions under ~/.claude/projects
cc-cost

# A specific transcript
cc-cost ~/.claude/projects/-home-foo/<id>.jsonl

# Single project
cc-cost --project -home-foo

# JSON output (for automation)
cc-cost --json

# Get specific cost-optimization recommendations
cc-cost --diagnose <transcript.jsonl>

# Show only the top 5 most expensive sessions
cc-cost --top 5
```

## Sample output

```
=== <session>.jsonl ===
  model:           claude-opus-4-7
  turns:           62
  input tokens:             232
  output tokens:         10,016
  cache write:          216,089
  cache read:         2,179,125
  cache hit rate:         91.0%   (higher = cheaper)
  total cost USD:  $     8.0750
  tool calls:
    Bash                           40
    Write                          2
    ToolSearch                     1
  top 5 expensive turns:
    $ 0.6993  [...]
```

## Why

When debugging "why was this session $40," the answer is almost always one of:
- Low cache hit rate (long static context not marked `cache_control`)
- Many redundant tool calls (each one re-pays the prompt)
- Long-output explanations the model generated unprompted

This shows you which of those is biting you, fast.

## Tip jar

If this saved you money, consider tipping:

- **Venmo**: [@lobsterlabs](https://venmo.com/u/lobsterlabs)
- **USDC on Base / any EVM**: `0xE0c311585d2000afF6b8020e30912Ac37ffe406a`
- **USDC on Solana / SOL**: `4a6YaVijdv79iXvXvXFu67kVBPFA6n8YSwYXt3ECj6ND`

## License

MIT.

