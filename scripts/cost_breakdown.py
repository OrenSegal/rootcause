"""Where the recorded costs come from, and what the 25x spread is made of.

    uv run python scripts/cost_breakdown.py

Three things, all from committed rows:

1. The per-token rates each sweep's recorded costs imply, solved exactly from four runs
   per model and checked against every other run, next to the rates in the price table
   (`adapters/anthropic_provider.py`). Claude Code's own loop reports its cost itself;
   the hand-written loop and the LangGraph port price recorded tokens with the table.
2. The loop and LangGraph sweeps repriced at the rates Claude Code reported for the same
   model, since those turns also went through Claude Code.
3. Cost per solved incident, Opus high over Haiku, split into its three factors:
   price per token, tokens per run and pass rate.
"""

import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from rootcause.adapters.anthropic_provider import PRICES
from rootcause.application.sweep import Row
from rootcause.composition import read_sweep

SWEEPS = Path(__file__).resolve().parent.parent / "results" / "sweeps"
MODELS = "models-and-retrieval-2026-10-15"
FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
TABLE = ("input", "output", "cache_read", "cache_write")


def tokens(row: Row) -> list[int]:
    return [getattr(row.grade.usage, f) for f in FIELDS]


def micro_usd(row: Row) -> Fraction:
    return Fraction(str(row.grade.cost_usd)) * 1_000_000


def solve(a: list[list[Fraction]], b: list[Fraction]) -> list[Fraction] | None:
    """Gauss-Jordan on a square system, exact. None when the rows are dependent."""
    n = len(a)
    m = [[*row, rhs] for row, rhs in zip(a, b, strict=True)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if m[r][col] != 0), None)
        if pivot is None:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        m[col] = [x / m[col][col] for x in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0:
                m[r] = [x - m[r][col] * y for x, y in zip(m[r], m[col], strict=True)]
    return [row[n] for row in m]


def implied_rates(rows: list[Row]) -> tuple[list[float], float] | None:
    """USD per million tokens (input, output, cache read, cache write) that reproduce the
    recorded costs, and the largest relative miss over all the rows."""
    for start in range(len(rows) - 3):
        pick = rows[start : start + 4]
        rates = solve(
            [[Fraction(t) for t in tokens(r)] for r in pick], [micro_usd(r) for r in pick]
        )
        if rates is not None:
            break
    else:
        return None
    miss = max(
        abs(sum(Fraction(t) * p for t, p in zip(tokens(r), rates, strict=True)) / micro_usd(r) - 1)
        for r in rows
    )
    return [float(p) for p in rates], float(miss)


def priced(row: Row, rates: list[float]) -> float:
    return sum(t * p for t, p in zip(tokens(row), rates, strict=True)) / 1_000_000


def by_model(rows: list[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if len(r.grade.models) == 1 and r.grade.cost_usd > 0:
            out[r.grade.models[0]].append(r)
    return out


def self_check() -> None:
    assert solve([[Fraction(2), Fraction(0)], [Fraction(0), Fraction(4)]], [Fraction(2)] * 2) == [
        1,
        Fraction(1, 2),
    ]
    assert (
        solve([[Fraction(1), Fraction(1)], [Fraction(2), Fraction(2)]], [Fraction(1)] * 2) is None
    )


def main() -> None:
    self_check()
    claude_code: dict[str, list[float]] = {}
    print("Recorded cost against the price table. Rates are USD per million tokens:")
    print("input, output, cache read, cache write.")
    for name in sorted(p.name for p in SWEEPS.iterdir() if (p / "rows.jsonl").exists()):
        rows, _ = read_sweep(SWEEPS / name)
        for model, mine in sorted(by_model(rows).items()):
            table = [getattr(PRICES[model], f) for f in TABLE]
            off = max(abs(priced(r, table) / r.grade.cost_usd - 1) for r in mine)
            line = f"  {name:36} {model:18} {len(mine):3} runs  "
            if off < 1e-3:  # recorded costs are rounded to a millionth of a dollar
                print(line + f"price table reproduces every run (max miss {off:.0e})")
                continue
            fit = implied_rates(mine)
            assert fit is not None, (name, model)
            rates, miss = fit
            if name == MODELS:
                claude_code[model] = rates
            print(
                line
                + "implied "
                + " ".join(f"{p:.2f}" for p in rates)
                + f" (max miss {miss:.0e}); price table "
                + " ".join(f"{p:.2f}" for p in table)
                + f" is off by up to {off:.0%}"
            )
    claude_code.setdefault(
        "claude-haiku-5-5", [getattr(PRICES["claude-haiku-5-5"], f) for f in TABLE]
    )

    print("\nLoop and LangGraph sweeps, cost per solved incident as recorded and at the rates")
    print("Claude Code reported for the same model in the models sweep:")
    for name in ("loop-2026-10-15", "langgraph-2026-10-15", "loop-haiku-2026-10-26"):
        rows, _ = read_sweep(SWEEPS / name)
        configs: dict[str, list[Row]] = defaultdict(list)
        for r in rows:
            configs[r.config].append(r)
        for config, mine in configs.items():
            solved = sum(r.grade.passed for r in mine)
            recorded = sum(r.grade.cost_usd for r in mine) / solved
            again = sum(priced(r, claude_code[r.grade.models[0]]) for r in mine) / solved
            print(
                f"  {config:32} recorded ${recorded:.4f}  repriced ${again:.4f}  "
                f"({again / recorded:.2f}x)"
            )

    rows, _ = read_sweep(SWEEPS / MODELS)
    stats = {}
    for config in ("opus-5.5-high", "haiku-5.5-high"):
        mine = [r for r in rows if r.config == config]
        cost, toks = sum(r.grade.cost_usd for r in mine), sum(sum(tokens(r)) for r in mine)
        stats[config] = (
            cost / toks,
            toks / len(mine),
            sum(r.grade.passed for r in mine) / len(mine),
        )
    (po, to, ro), (ph, th, rh) = stats["opus-5.5-high"], stats["haiku-5.5-high"]
    print("\nCost per solved incident, Opus high over Haiku, models sweep:")
    print(f"  price per token (all token kinds blended)  {po / ph:5.2f}x")
    print(f"  tokens per run                             {to / th:5.2f}x")
    print(f"  runs per solved incident (1 / pass rate)   {rh / ro:5.2f}x")
    print(f"  product                                    {po / ph * to / th * rh / ro:5.2f}x")


if __name__ == "__main__":
    sys.exit(main())
