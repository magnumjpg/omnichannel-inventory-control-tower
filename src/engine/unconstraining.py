"""Right-censored demand unconstraining.

Three estimators are computed for every SKU:

1. **Active-day extrapolation** with the continuous exposure weight
   ``tau = min(1, S / (max(S, mu_prev) + 0.5 sigma_prev))`` for partial-day
   stockouts.  This is the maximum-likelihood rate for a thinned Poisson
   process and is the *shipped* estimate whenever ``tau > 0``.
2. **Cross-sectional sister-SKU imputation** using the stationary parent
   share ``omega_i|P`` and a cannibalisation deflator ``gamma_P``.
3. **Empirical-Bayes credibility shrinkage** (James-Stein style) with
   ``kappa = 7``, retained as a diagnostic column only.

The shipped fallback ladder is ``tau > 0 -> mu_active``, ``tau = 0 ->
baseline_90`` (a steadier anchor for deliberately depleted long-tail SKUs),
then the lifetime 365-day rate, and the sister prior only as a last resort
when the SKU was never exposed in the available history.
``src/diagnostics/estimator_backtest.py`` measures every candidate walk-forward
on nine monthly cutoffs; the kappa blend is kept as a rejected alternative
because it added bias without reducing WAPE.

The module also exposes the Type-I right-censored Tobit expectation and the
inverse Mills ratio for audit / research purposes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src import config

# The catalogue's ABC-XYZ design carries a published coefficient-of-variation
# envelope for each tier.  The floor prevents a short, unusually calm window
# from under-buffering a hero; the cap prevents censored zero-sales days from
# masquerading as genuine volatility (a right-censored day is a *lower bound*
# on demand, not a demand observation).
TIER_SIGMA_FLOOR = {
    "AX": 0.20,
    "AY": 0.30,
    "BX": 0.30,
    "BY": 0.45,
    "CZ": 0.65,
}
TIER_SIGMA_CAP = {
    "AX": 0.32,
    "AY": 0.45,
    "BX": 0.50,
    "BY": 0.68,
    "CZ": 1.35,
}


# ---------------------------------------------------------------------------
# Mathematical primitives
# ---------------------------------------------------------------------------
def normal_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def inverse_mills_ratio(z: float) -> float:
    """Right-tail inverse Mills ratio ``phi(z) / (1 - Phi(z))``."""

    survival = 1.0 - normal_cdf(z)
    if survival <= 1e-12:
        # Asymptotic tail approximation avoids division by zero.
        return max(z, 0.0) + 1.0 / max(z, 1e-6)
    return normal_pdf(z) / survival


def tobit_censored_expectation(
    mu: float,
    sigma: float,
    censoring_threshold: float,
) -> float:
    """``E[D* | D* >= I]`` for a Type-I right-censored normal demand model."""

    if sigma <= 0:
        return max(mu, censoring_threshold)
    z = (censoring_threshold - mu) / sigma
    return mu + sigma * inverse_mills_ratio(z)


def credibility_blend(
    mu_active: float,
    mu_cs: float,
    n_active: float,
    kappa: float = config.KAPPA_CREDIBILITY,
) -> float:
    """Empirical-Bayes blend.  At ``n_active == kappa`` the weights are 50/50."""

    denom = max(n_active + kappa, 1e-9)
    return (n_active / denom) * mu_active + (kappa / denom) * mu_cs


def continuous_exposure(
    sales: float,
    opening_inv: float,
    closing_inv: float,
    mu_prev: float,
    sigma_prev: float,
) -> float:
    """Continuous exposure weight for one SKU-day.

    A fully in-stock day receives weight 1.  A zero-inventory day receives 0.
    Only a partial-day stockout (positive opening, zero closing) invokes the
    survival approximation.
    """

    if opening_inv <= 0:
        return 0.0
    if closing_inv > 0:
        return 1.0
    denom = max(sales, mu_prev) + 0.5 * max(sigma_prev, 0.0)
    if denom <= 0:
        return 0.0
    return float(min(1.0, max(0.0, sales / denom)))


def cross_sectional_impute(
    omega_i: float,
    sister_sales: float,
    active_omega_sum: float,
    inactive_omega_sum: float,
    gamma: float = config.GAMMA_CANNIBALIZATION,
) -> float:
    """Cross-sectional demand for a depleted child SKU.

    ``active_omega_sum`` covers only the *sisters* that are in stock;
    ``inactive_omega_sum`` includes the depleted SKU itself and every other
    unavailable sibling.  ``gamma`` is the substitution spillover: the sister
    sales are inflated by this fraction, so the denominator is deflated.
    """

    denom = active_omega_sum + gamma * inactive_omega_sum
    if denom <= 1e-12 or sister_sales <= 0:
        return 0.0
    return float(omega_i * sister_sales / denom)


# ---------------------------------------------------------------------------
# Channel estimation
# ---------------------------------------------------------------------------
def _estimate_channel(
    daily: pd.DataFrame,
    catalog: pd.DataFrame,
    channel: str,
    window: int = 30,
) -> pd.DataFrame:
    d = daily[daily["channel"] == channel].copy()
    if d.empty:
        raise ValueError(f"no daily rows for channel {channel}")
    d["activity_date"] = pd.to_datetime(d["activity_date"])
    all_dates = np.sort(d["activity_date"].unique())
    if len(all_dates) < window:
        raise ValueError("history shorter than the requested window")
    w_dates = set(all_dates[-window:])
    h_dates = set(all_dates[-90:])
    # Widest available exposure window.  A SKU that has been dark for the whole
    # 90-day baseline still had a life before that; its lifetime active-day rate
    # is the only self-referential information left, and it is a far better
    # anchor than a sister prior computed while its whole family was dark.
    y_dates = set(all_dates[-min(len(all_dates), 365):])

    # Parent-level pivots make the daily sister-SKU scan O(days x parent).
    sales_pivot = d.pivot_table(
        index="activity_date", columns="sku_id", values="observed_sales", aggfunc="sum"
    ).fillna(0.0)
    active_pivot = d.pivot_table(
        index="activity_date", columns="sku_id", values="in_stock_flag", aggfunc="min"
    ).fillna(0)

    omega = catalog.set_index("sku_id")["parent_stationary_share"].to_dict()
    parent_of = catalog.set_index("sku_id")["parent_sku_id"].to_dict()
    parents: dict[str, list[str]] = {}
    for sku, parent in parent_of.items():
        parents.setdefault(parent, []).append(sku)

    rows: list[dict] = []
    for sku in catalog["sku_id"]:
        parent = parent_of[sku]
        siblings = [s for s in parents[parent] if s != sku]
        s = d[d["sku_id"] == sku].set_index("activity_date").sort_index()
        s_w = s.loc[s.index.isin(w_dates)]
        s_h = s.loc[s.index.isin(h_dates)]
        sales_30 = float(s_w["observed_sales"].sum())
        tau_30 = float(s_w["effective_active_day_weight"].clip(0, 1).sum())
        naive = sales_30 / window
        stockout_days = int((s_w["in_stock_flag"] == 0).sum())
        mu_active = sales_30 / tau_30 if tau_30 > 1e-9 else 0.0

        tau_h = float(s_h["effective_active_day_weight"].clip(0, 1).sum())
        baseline_90 = (
            float(s_h["observed_sales"].sum()) / tau_h if tau_h > 1e-9 else 0.0
        )

        s_y = s.loc[s.index.isin(y_dates)]
        tau_y = float(s_y["effective_active_day_weight"].clip(0, 1).sum())
        baseline_365 = (
            float(s_y["observed_sales"].sum()) / tau_y if tau_y > 1e-9 else 0.0
        )

        # --- daily cross-sectional imputation on censored days -----------
        imputed: list[float] = []
        omega_i = float(omega.get(sku, 0.0))
        for day in sorted(w_dates):
            if day not in s.index:
                continue
            row = s.loc[day]
            if int(row["in_stock_flag"]) == 1 and float(row["effective_active_day_weight"]) >= 0.999:
                continue
            if day not in active_pivot.index:
                continue
            sister_sales = 0.0
            active_omega = 0.0
            for sis in siblings:
                if sis not in active_pivot.columns:
                    continue
                if int(active_pivot.loc[day, sis]) == 1:
                    sister_sales += float(sales_pivot.loc[day, sis])
                    active_omega += float(omega.get(sis, 0.0))
            inactive_omega = max(0.0, 1.0 - active_omega)
            val = cross_sectional_impute(
                omega_i, sister_sales, active_omega, inactive_omega
            )
            if val > 0:
                imputed.append(val)

        if imputed:
            mu_cs = float(np.mean(imputed))
        elif mu_active > 0:
            mu_cs = mu_active
        else:
            mu_cs = baseline_90

        if tau_30 > 1e-9:
            # The tau-weighted active-day rate is the maximum-likelihood rate
            # for a thinned Poisson process.  The kappa blend was measured
            # walk-forward and rejected: it added bias without reducing WAPE.
            mu_final = mu_active
        elif baseline_90 > 0:
            # Dark for the whole 30-day window, but exposed inside 90 days.
            mu_final = baseline_90
        elif baseline_365 > 0:
            # Dark for 90 days as well: fall back to the lifetime active-day
            # rate.  Widening the window is the only self-referential evidence
            # left, and it avoids the sister-prior blow-up described below.
            mu_final = baseline_365
        else:
            # Never exposed in the available history: the sister prior is the
            # only signal left.  It is the last resort because its denominator
            # collapses to ``gamma`` when every sibling is also out of stock,
            # which multiplies the estimate by roughly ``1 / gamma``.
            mu_final = mu_cs

        # --- volatility ---------------------------------------------------
        # Censored and zero-inventory days are excluded from the volatility
        # sample: including them would conflate a stockout with a demand
        # collapse and systematically overstate sigma (and therefore SS1/SS2).
        active_sales = s_w.loc[
            s_w["effective_active_day_weight"] > 0, "observed_sales"
        ].to_numpy(dtype=float)
        sigma_active = float(np.std(active_sales, ddof=1)) if len(active_sales) > 1 else 0.0
        hist_active = s_h.loc[
            s_h["effective_active_day_weight"] > 0, "observed_sales"
        ].to_numpy(dtype=float)
        hist_mean = float(hist_active.mean()) if len(hist_active) else 0.0
        cv_prior = (
            float(hist_active.std(ddof=1) / hist_mean)
            if hist_mean > 0 and len(hist_active) > 1
            else 0.0
        )
        tier = catalog.loc[catalog["sku_id"] == sku, "abc_xyz_tier"].iloc[0]
        cv_floor = TIER_SIGMA_FLOOR.get(tier, 0.5)
        cv_cap = TIER_SIGMA_CAP.get(tier, 1.5)
        cv_estimate = min(max(cv_prior, cv_floor), cv_cap)
        sigma = min(
            max(sigma_active, cv_estimate * mu_final),
            cv_cap * mu_final,
        )

        rows.append(
            {
                "sku_id": sku,
                "naive_30d": round(naive, 4),
                "active_days_30d": round(tau_30, 4),
                "mu_active": round(mu_active, 4),
                "mu_cs": round(mu_cs, 4),
                "mu_channel": round(mu_final, 4),
                "sigma_channel": round(sigma, 4),
                "stockout_days_30d": stockout_days,
                "baseline_90d": round(baseline_90, 4),
                "baseline_365d": round(baseline_365, 4),
            }
        )
    return pd.DataFrame(rows)


def estimate_unconstrained_demand(
    daily: pd.DataFrame,
    catalog: pd.DataFrame,
    window: int = 30,
    kappa: float = config.KAPPA_CREDIBILITY,
) -> pd.DataFrame:
    """Run the three-tier estimator independently on FBA and Shopify DTC."""

    fba = _estimate_channel(daily, catalog, "AMZ_US", window=window)
    dtc = _estimate_channel(daily, catalog, "SHOPIFY_DTC", window=window)
    out = catalog[
        ["sku_id", "parent_sku_id", "abc_xyz_tier", "z_score", "fba_channel_share"]
    ].copy()
    out = out.merge(
        fba.rename(
            columns={
                "naive_30d": "naive_30d_fba",
                "active_days_30d": "active_days_30d_fba",
                "mu_active": "mu_active_fba",
                "mu_cs": "mu_cs_fba",
                "mu_channel": "mu_fba",
                "sigma_channel": "sigma_fba",
                "stockout_days_30d": "stockout_days_30d_fba",
                "baseline_90d": "baseline_90d_fba",
                "baseline_365d": "baseline_365d_fba",
            }
        ),
        on="sku_id",
        how="left",
    )
    out = out.merge(
        dtc.rename(
            columns={
                "naive_30d": "naive_30d_dtc",
                "active_days_30d": "active_days_30d_dtc",
                "mu_active": "mu_active_dtc",
                "mu_cs": "mu_cs_dtc",
                "mu_channel": "mu_dtc",
                "sigma_channel": "sigma_dtc",
                "stockout_days_30d": "stockout_days_30d_dtc",
                "baseline_90d": "baseline_90d_dtc",
                "baseline_365d": "baseline_365d_dtc",
            }
        ),
        on="sku_id",
        how="left",
    )
    out = out.rename(
        columns={
            "naive_30d_fba": "naive_30d",
            "active_days_30d_fba": "active_days_30d",
            "mu_active_fba": "mu_active",
            "mu_cs_fba": "mu_cs",
            "mu_fba": "mu_unconstrained",
            "sigma_fba": "sigma_unconstrained",
            "stockout_days_30d_fba": "stockout_days_30d",
            "baseline_90d_fba": "baseline_90d",
            "baseline_365d_fba": "baseline_365d",
        }
    )
    out["mu_global"] = out["mu_unconstrained"] + out["mu_dtc"]
    out["sigma_global"] = np.sqrt(out["sigma_unconstrained"] ** 2 + out["sigma_dtc"] ** 2)
    out["pct_stockout_30d"] = out["stockout_days_30d"] / float(window)
    out["unconstraining_uplift"] = np.where(
        out["naive_30d"] > 0,
        out["mu_unconstrained"] / out["naive_30d"] - 1.0,
        0.0,
    )
    numeric = [
        "naive_30d", "active_days_30d", "mu_active", "mu_cs",
        "mu_unconstrained", "sigma_unconstrained", "mu_dtc", "sigma_dtc",
        "mu_global", "sigma_global", "baseline_90d", "baseline_365d",
    ]
    for col in numeric:
        out[col] = out[col].astype(float).round(4)
    return out.sort_values(["abc_xyz_tier", "mu_global"], ascending=[True, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Convenience dataclass for reporting / tests
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnconstrainedVelocity:
    sku_id: str
    naive: float
    active: float
    cross_sectional: float
    final: float
    sigma: float
    active_days: float
