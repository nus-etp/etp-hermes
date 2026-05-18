#!/usr/bin/env python3
"""Render per-company metric charts from data/metrics/<slug>.jsonl.

For each JSONL file, emit a small multi-panel PNG to signals/metrics/<slug>.png.
A panel is drawn only if the series has at least one non-null, non-zero value
within the rolling window (default 90 days). Companies with no signal at all
are skipped — no PNG is written, keeping signals/metrics/ to companies that
actually have a story to tell.

Deterministic. Pure-Python + matplotlib (Agg backend so it works headless).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_PATH = REPO_ROOT / "data" / "companies.json"
METRICS_DIR = REPO_ROOT / "data" / "metrics"
CHARTS_DIR = REPO_ROOT / "signals" / "metrics"
WINDOW_DAYS = 90


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def extract_series(rows: list[dict[str, Any]], path: list[str]) -> tuple[list[dt.date], list[float | None]]:
    xs: list[dt.date] = []
    ys: list[float | None] = []
    for r in rows:
        v: Any = r
        for key in path:
            if not isinstance(v, dict):
                v = None
                break
            v = v.get(key)
        if isinstance(v, dict):
            v = None
        xs.append(parse_date(r["date"]))
        ys.append(v if (v is None or isinstance(v, (int, float))) else None)
    return xs, ys


def has_signal(ys: list[float | None]) -> bool:
    return any(y is not None and y != 0 for y in ys)


def filter_window(xs: list[dt.date], ys: list[float | None], cutoff: dt.date) -> tuple[list[dt.date], list[float | None]]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x >= cutoff]
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def plot_series(ax: plt.Axes, xs: list[dt.date], ys: list[float | None], label: str, color: str) -> None:
    # Drop None values for plotting; keep the rest as a contiguous line with markers.
    clean = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if not clean:
        ax.text(0.5, 0.5, "no data", transform=ax.transAxes, ha="center", va="center", color="#999")
        return
    cx = [c[0] for c in clean]
    cy = [c[1] for c in clean]
    ax.plot(cx, cy, marker="o", markersize=3, linewidth=1.4, color=color, label=label)
    ax.fill_between(cx, 0, cy, alpha=0.12, color=color)
    ax.set_ylabel(label, fontsize=8)
    ax.tick_params(axis="both", labelsize=7)
    ax.grid(True, axis="y", linestyle=":", alpha=0.4)
    ax.margins(x=0.02)
    ymax = max(cy)
    if ymax == 0:
        ax.set_ylim(0, 1)
    else:
        ax.set_ylim(0, ymax * 1.15 + 1)


def render_company(name: str, slug: str, rows: list[dict[str, Any]], render_date: dt.date) -> bool:
    cutoff = render_date - dt.timedelta(days=WINDOW_DAYS)

    panels: list[tuple[str, str, list[dt.date], list[float | None]]] = []
    # (label, color, xs, ys)
    for label, color, path in (
        ("GitHub stars",   "#1f77b4", ["github", "stars"]),
        ("GitHub followers", "#9467bd", ["github", "followers"]),
        ("Open jobs",      "#2ca02c", ["lever", "open"]),
        ("HN mentions (30d)", "#ff7f0e", ["hn_30d"]),
        ("GDELT articles (7d)", "#d62728", ["gdelt_7d"]),
    ):
        xs, ys = extract_series(rows, path)
        xs, ys = filter_window(xs, ys, cutoff)
        if has_signal(ys):
            panels.append((label, color, xs, ys))

    if not panels:
        return False

    fig, axes = plt.subplots(
        nrows=len(panels), ncols=1, figsize=(8, 1.6 * len(panels) + 0.8), sharex=True
    )
    if len(panels) == 1:
        axes = [axes]
    for ax, (label, color, xs, ys) in zip(axes, panels):
        plot_series(ax, xs, ys, label, color)

    # Clamp x-axis to the rolling window so single-datapoint charts don't
    # auto-scale across multiple years.
    for ax in axes:
        ax.set_xlim(cutoff, render_date)
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    for tick in axes[-1].get_xticklabels():
        tick.set_rotation(0)

    fig.suptitle(f"{name} — public-attention metrics  ·  {render_date.isoformat()}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / f"{slug}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return True


def load_name_by_slug() -> dict[str, str]:
    import re

    def slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    companies = json.loads(COMPANIES_PATH.read_text())
    return {slugify(c["name"]): c["name"] for c in companies}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date used as 'today' for the rolling window (default: today).",
    )
    args = parser.parse_args()
    render_date = dt.date.fromisoformat(args.date)

    if not METRICS_DIR.exists():
        print(f"no metrics dir at {METRICS_DIR}; nothing to render")
        return 0

    name_by_slug = load_name_by_slug()
    rendered = 0
    skipped = 0
    for jsonl in sorted(METRICS_DIR.glob("*.jsonl")):
        slug = jsonl.stem
        rows = load_jsonl(jsonl)
        if not rows:
            skipped += 1
            continue
        name = name_by_slug.get(slug, slug)
        if render_company(name, slug, rows, render_date):
            rendered += 1
            print(f"  + {slug}.png")
        else:
            skipped += 1

    print(f"rendered: {rendered}, skipped (no signal): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
