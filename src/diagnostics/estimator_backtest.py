"""Walk-forward backtest of the unconstrained-demand estimator.

The estimator answers a counterfactual question -- "what would this SKU have
sold with unlimited stock?" -- so it cannot be scored against observed sales.
It is scored against the generator's *latent* FBA demand, which is why this
module re-runs the deterministic simulation instead of reading the committed
feeds: it needs the demand the simulator drew before inventory truncated it.

Design notes
------------
* Nine monthly cutoffs, not one.  A single window is where the fallback rule
  was chosen; re-scoring on the same window would be in-sample.  Each cutoff
  sees only the history available on that date, exactly as production would.
* Four candidates are scored: the shipped ladder, the rejected kappa blend,
  the naive run-rate and the sister prior.
* The target is the latent rate over the *estimation* window, not a forward
  forecast.  Scoring forward would conflate the estimator with seasonality.
* This is a recovery test against a known data-generating process.  It has no
  sampling error in the target, so it is a best case, and the README says so.

Run with ``python -m src.diagnostics.estimator_backtest``.
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src import config
from src.data.generator import _simulate_inventory, build_catalog, generate_all
from src.engine.unconstraining import (
    credibility_blend,
    estimate_unconstrained_demand,
)

WINDOW = 30

# Last day of each month with at least one full 90-day history behind it,
# plus the live snapshot.  Feb is the first month where the 90-day baseline
# is not truncated by the start of the simulated history (2025-10-26).
CUTOFFS: tuple[date, ...] = (
    date(2026, 2, 28),
    date(2026, 3, 31),
    date(2026, 4, 30),
    date(2026, 5, 31),
    date(2026, 6, 30),
    date(2026, 7, 31),
    date(2026, 8, 31),
    date(2026, 9, 30),
    date(2026, 10, 25),
)

METHOD_LABELS = {
    "shipped": "Shipped ladder (active-day, widening fallback)",
    "kappa_blend": "Rejected kappa=7 blend",
    "naive": "Naive run-rate (sales / 30)",
    "sister": "Sister prior only",
}


def _candidates(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Return the shipped estimate and the three rejected/benchmark variants."""

    tau = frame["active_days_30d"].to_numpy(dtype=float)
    mu_active = frame["mu_active"].to_numpy(dtype=float)
    mu_cs = frame["mu_cs"].to_numpy(dtype=float)
    censored = tau <= 1e-9

    kappa = np.where(
        censored,
        mu_cs,
        [
            credibility_blend(a, c, t)
            for a, c, t in zip(mu_active, mu_cs, tau, strict=True)
        ],
    )
    return {
        # Scored straight from the production column, not a reimplementation.
        "shipped": frame["mu_unconstrained"].to_numpy(dtype=float),
        "kappa_blend": kappa,
        "naive": frame["naive_30d"].to_numpy(dtype=float),
        "sister": mu_cs,
    }


def _metrics(estimate: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    bias = float(estimate.sum() / truth.sum() - 1.0) * 100.0
    wape = float(np.abs(estimate - truth).sum() / truth.sum()) * 100.0
    ape = np.abs(estimate - truth) / truth
    return {
        "bias_pct": bias,
        "wape_pct": wape,
        "median_ape_pct": float(np.median(ape)) * 100.0,
    }


def run(cutoffs: tuple[date, ...] = CUTOFFS) -> pd.DataFrame:
    """Score every candidate on every cutoff and return the long-form table."""

    catalog, truth = build_catalog(seed=config.SEED)
    dates = [
        config.SNAPSHOT_DATE - timedelta(days=config.HISTORY_DAYS - 1 - i)
        for i in range(config.HISTORY_DAYS)
    ]
    sim = _simulate_inventory(catalog, truth, dates, np.random.default_rng(config.SEED))
    latent = np.asarray(sim["fba_demand"], dtype=float)
    assert latent.shape == (len(catalog), len(dates)), latent.shape
    position = {day: idx for idx, day in enumerate(dates)}

    with tempfile.TemporaryDirectory(prefix="backtest_") as tmp:
        tmp_path = Path(tmp)
        generate_all(output_dir=tmp_path, seed=config.SEED, processed_dir=tmp_path)
        daily_all = pd.read_csv(tmp_path / "daily_sales_history.csv")
        daily_all["activity_date"] = pd.to_datetime(daily_all["activity_date"]).dt.date

        rows: list[dict[str, object]] = []
        for cutoff in cutoffs:
            history = daily_all[daily_all["activity_date"] <= cutoff]
            frame = estimate_unconstrained_demand(history, catalog, window=WINDOW)
            frame = frame.set_index("sku_id").reindex(catalog["sku_id"].values)

            end = position[cutoff] + 1
            start = end - WINDOW
            target = latent[:, start:end].mean(axis=1)

            censored_mask = frame["active_days_30d"].to_numpy(dtype=float) <= 1e-9
            for method, estimate in _candidates(frame).items():
                overall = _metrics(estimate, target)
                cens = _metrics(estimate[censored_mask], target[censored_mask])
                rows.append(
                    {
                        "cutoff": cutoff.isoformat(),
                        "method": method,
                        "n_skus": int(len(target)),
                        "n_censored": int(censored_mask.sum()),
                        **{k: round(v, 3) for k, v in overall.items()},
                        "censored_bias_pct": round(cens["bias_pct"], 3),
                        "censored_wape_pct": round(cens["wape_pct"], 3),
                    }
                )

    return pd.DataFrame(rows)


def summarise(table: pd.DataFrame) -> pd.DataFrame:
    """Mean metrics per method across cutoffs."""

    grouped = (
        table.groupby("method", as_index=False)
        .agg(
            mean_bias_pct=("bias_pct", "mean"),
            mean_abs_bias_pct=("bias_pct", lambda s: float(np.abs(s).mean())),
            mean_wape_pct=("wape_pct", "mean"),
            worst_wape_pct=("wape_pct", "max"),
            mean_censored_wape_pct=("censored_wape_pct", "mean"),
        )
        .sort_values("mean_wape_pct")
    )
    return grouped.round(3)


def to_markdown(table: pd.DataFrame, summary: pd.DataFrame) -> str:
    labels = METHOD_LABELS
    head = [
        "# Unconstrained-demand estimator: walk-forward backtest",
        "",
        "Generated by `python -m src.diagnostics.estimator_backtest`.",
        "Scored against the generator's latent FBA demand on nine monthly cutoffs;",
        "each cutoff sees only the history available on that date.",
        "",
        "## Mean performance across cutoffs",
        "",
        "| Method | Mean bias | Mean abs. bias | Mean WAPE | Worst WAPE | Censored WAPE |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary.itertuples(index=False):
        head.append(
            f"| {labels[row.method]} | {row.mean_bias_pct:+.1f}% | "
            f"{row.mean_abs_bias_pct:.1f}% | {row.mean_wape_pct:.1f}% | "
            f"{row.worst_wape_pct:.1f}% | {row.mean_censored_wape_pct:.1f}% |"
        )

    pivot = table.pivot(index="method", columns="cutoff", values="wape_pct")
    head += [
        "",
        "## WAPE by cutoff (%)",
        "",
        "| Method | " + " | ".join(pivot.columns) + " |",
        "| --- | " + " | ".join("---:" for _ in pivot.columns) + " |",
    ]
    for method, row in pivot.iterrows():
        cells = " | ".join(f"{value:.1f}" for value in row.to_numpy())
        head.append(f"| {labels[method]} | {cells} |")

    head += [
        "",
        "## How to read this",
        "",
        "- The shipped ladder is the pre-specified rule: active-day rate when the",
        "  SKU traded at all in the window, 90-day active-day rate when it was",
        "  fully depleted, sister prior only as a last resort.",
        "- The kappa=7 blend is the estimator this project shipped first. It lost",
        "  on both bias and WAPE, so it is retained as a diagnostic column only.",
        "- The naive run-rate is the do-nothing benchmark. It is systematically",
        "  biased low because a stockout day counts as a zero-sales day.",
        "- The target is latent demand, not observed sales: this is a recovery",
        "  test against a known data-generating process, so it has no target",
        "  sampling error and should be read as a best case.",
        "",
    ]
    return "\n".join(head)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    table = run()
    summary = summarise(table)

    csv_path = config.DATA_PROCESSED_DIR / "estimator_backtest.csv"
    md_path = config.OUTPUTS_DIR / "estimator_backtest.md"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(csv_path, index=False)
    md_path.write_text(to_markdown(table, summary), encoding="utf-8")

    if not args.quiet:
        print(summary.to_string(index=False))
        print(f"\n  table -> {csv_path}")
        print(f"  notes -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
