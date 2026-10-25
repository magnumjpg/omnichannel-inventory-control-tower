"""Reproducible synthetic ERP / WMS / marketplace telemetry generator.

The generator builds a 160-child-SKU, 365-day omnichannel history with:

* a four-dimensional attribute matrix (material x size x colour x hardware),
* a Pareto ABC-XYZ catalogue calibrated to the 73.5% / 19.3% / 7.2% volume
  split documented in the research dossier,
* right-censored channel demand (``S = min(D, I)``),
* weekly 3PL -> FBA transfers and factory -> 3PL purchase orders,
* nine explicit stress scenarios: 3 critical FNSKUs (<14d HDoS), 4 in the
  14-28d fee-penalty band, and 2 aged-inventory cliff FNSKUs (>270d).

Everything is seeded, so repeated runs produce byte-identical CSV files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from src import config

# ---------------------------------------------------------------------------
# Catalogue specification
# ---------------------------------------------------------------------------
COLOR_CODE = {
    "Black": "BLK",
    "Navy": "NVY",
    "Olive": "OLV",
    "Slate": "SLT",
    "Sand": "SND",
    "Terracotta": "TRC",
}
HARDWARE_CODE = {
    "Standard Zip": "ZIP",
    "Water-Resistant Zip": "WRZ",
    "Drawstring": "DST",
    "Zip": "ZIP",
    "None": "NA",
}
SIZE_CODE = {
    "S": "S",
    "M": "M",
    "L": "L",
    "Compact": "CMP",
    "Large": "LRG",
    "Standard": "STD",
}


@dataclass(frozen=True)
class FamilySpec:
    code: str
    name: str
    parent_sku_id: str
    material: str
    material_code: str
    sizes: tuple[tuple[str, float], ...]
    colors: tuple[tuple[str, float], ...]
    hardware: tuple[tuple[str, float], ...]
    channel_fba_share: float
    master_carton_qty: int
    carton_volume_cbm: float
    carton_weight_kg: float
    sku_moq_units: int
    fob_unit_cost: float
    landed_cost: float
    retail_price: float
    unit_volume_cuft: float
    family_weight: float
    supplier_id: str
    seasonal_amplitude: float = 1.0


FAMILIES: tuple[FamilySpec, ...] = (
    FamilySpec(
        code="P01",
        name="Aero Packing Cubes",
        parent_sku_id="P01-PCK-CUBE",
        material="20D Ripstop Nylon",
        material_code="RPN",
        sizes=(("S", 0.12), ("M", 0.52), ("L", 0.36)),
        colors=(("Black", 0.38), ("Navy", 0.24), ("Olive", 0.14),
                ("Slate", 0.11), ("Sand", 0.08), ("Terracotta", 0.05)),
        hardware=(("Standard Zip", 0.68), ("Water-Resistant Zip", 0.32)),
        channel_fba_share=0.72,
        master_carton_qty=100,
        carton_volume_cbm=0.060,
        carton_weight_kg=12.20,
        sku_moq_units=300,
        fob_unit_cost=4.20,
        landed_cost=5.15,
        retail_price=40.00,
        unit_volume_cuft=0.00564,
        family_weight=0.30,
        supplier_id="SUP-SZ-01",
        seasonal_amplitude=1.00,
    ),
    FamilySpec(
        code="P02",
        name="Vault Compression Bags",
        parent_sku_id="P02-CMP-BAG",
        material="TPU-Laminated Ripstop",
        material_code="TPU",
        sizes=(("S", 0.12), ("M", 0.52), ("L", 0.36)),
        colors=(("Black", 0.38), ("Navy", 0.24), ("Olive", 0.14),
                ("Slate", 0.11), ("Sand", 0.08), ("Terracotta", 0.05)),
        hardware=(("Standard Zip", 0.68), ("Water-Resistant Zip", 0.32)),
        channel_fba_share=0.70,
        master_carton_qty=100,
        carton_volume_cbm=0.060,
        carton_weight_kg=12.20,
        sku_moq_units=300,
        fob_unit_cost=4.20,
        landed_cost=5.15,
        retail_price=40.00,
        unit_volume_cuft=0.00564,
        family_weight=0.26,
        supplier_id="SUP-SZ-02",
        seasonal_amplitude=1.08,
    ),
    FamilySpec(
        code="P03",
        name="Transit Toiletry Kits",
        parent_sku_id="P03-TLT-KIT",
        material="Recycled 600D Polyester",
        material_code="RPX",
        sizes=(("S", 0.12), ("M", 0.52), ("L", 0.36)),
        colors=(("Black", 0.38), ("Navy", 0.24), ("Olive", 0.14),
                ("Slate", 0.11), ("Sand", 0.08), ("Terracotta", 0.05)),
        hardware=(("Standard Zip", 0.68), ("Water-Resistant Zip", 0.32)),
        channel_fba_share=0.62,
        master_carton_qty=50,
        carton_volume_cbm=0.060,
        carton_weight_kg=12.20,
        sku_moq_units=300,
        fob_unit_cost=4.20,
        landed_cost=5.15,
        retail_price=40.00,
        unit_volume_cuft=0.00564,
        family_weight=0.14,
        supplier_id="SUP-NB-01",
        seasonal_amplitude=1.12,
    ),
    FamilySpec(
        code="P04",
        name="Grid Tech Organizers",
        parent_sku_id="P04-TCH-ORG",
        material="Recycled Felt",
        material_code="FLT",
        sizes=(("Compact", 0.60), ("Large", 0.40)),
        colors=(("Black", 0.38), ("Navy", 0.24), ("Olive", 0.14),
                ("Slate", 0.11), ("Sand", 0.08), ("Terracotta", 0.05)),
        hardware=(("Standard Zip", 0.68), ("Water-Resistant Zip", 0.32)),
        channel_fba_share=0.65,
        master_carton_qty=100,
        carton_volume_cbm=0.030,
        carton_weight_kg=9.40,
        sku_moq_units=500,
        fob_unit_cost=6.50,
        landed_cost=7.90,
        retail_price=49.00,
        unit_volume_cuft=0.00350,
        family_weight=0.08,
        supplier_id="SUP-SZ-03",
        seasonal_amplitude=1.00,
    ),
    FamilySpec(
        code="P05",
        name="Cabin Shoe Bags",
        parent_sku_id="P05-SHO-BAG",
        material="Waxed Cotton Canvas",
        material_code="WAX",
        sizes=(("Standard", 0.65), ("Large", 0.35)),
        colors=(("Black", 0.38), ("Navy", 0.14), ("Olive", 0.11),
                ("Sand", 0.08)),
        hardware=(("Drawstring", 0.68), ("Zip", 0.32)),
        channel_fba_share=0.55,
        master_carton_qty=50,
        carton_volume_cbm=0.099,
        carton_weight_kg=14.60,
        sku_moq_units=300,
        fob_unit_cost=8.20,
        landed_cost=9.60,
        retail_price=58.00,
        unit_volume_cuft=0.01200,
        family_weight=0.12,
        supplier_id="SUP-NB-02",
        seasonal_amplitude=1.18,
    ),
    FamilySpec(
        code="P06",
        name="Mesh Laundry Bags",
        parent_sku_id="P06-LAU-BAG",
        material="Recycled Poly Mesh",
        material_code="MSH",
        sizes=(("S", 0.25), ("M", 0.45), ("L", 0.30)),
        colors=(("Black", 0.38), ("Navy", 0.24), ("Olive", 0.11),
                ("Sand", 0.08)),
        hardware=(("Drawstring", 1.00),),
        channel_fba_share=0.45,
        master_carton_qty=50,
        carton_volume_cbm=0.084,
        carton_weight_kg=12.50,
        sku_moq_units=500,
        fob_unit_cost=9.40,
        landed_cost=11.20,
        retail_price=65.00,
        unit_volume_cuft=0.01100,
        family_weight=0.10,
        supplier_id="SUP-NB-03",
        seasonal_amplitude=1.35,
    ),
)

TIER_MASS = {"AX": 648.0, "AY": 234.0, "BX": 157.2, "BY": 74.4, "CZ": 86.4}
TIER_BOUNDS = {
    "AX": (22.0, 48.0),
    "AY": (14.0, 25.0),
    "BX": (3.0, 10.0),
    "BY": (1.5, 6.0),
    "CZ": (0.20, 2.20),
}
TIER_CV_BOUNDS = {
    "AX": (0.22, 0.32),
    "AY": (0.33, 0.45),
    "BX": (0.35, 0.50),
    "BY": (0.50, 0.68),
    "CZ": (0.75, 1.35),
}

CATALOG_COLUMNS = [
    "sku_id",
    "parent_sku_id",
    "asin",
    "fnsku",
    "shopify_variant_id",
    "material",
    "size_width",
    "color",
    "hardware",
    "abc_xyz_tier",
    "z_score",
    "master_carton_qty",
    "carton_volume_cbm",
    "carton_weight_kg",
    "sku_moq_units",
    "po_aggregate_moq_units",
    "fob_unit_cost",
    "landed_cost",
    "retail_price",
    "parent_stationary_share",
    # Extensions used by the Excel control tower (not part of the 3NF core,
    # but required to keep unit volumes and channel splits auditable).
    "unit_volume_cuft",
    "fba_channel_share",
    "fba_size_tier",
    "supplier_id",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _allocate_with_bounds(
    total: float,
    weights: np.ndarray,
    lower: float,
    upper: float,
    max_iter: int = 500,
) -> np.ndarray:
    """Allocate a mass across SKUs while respecting per-SKU velocity bounds.

    Proportional allocation is iteratively clipped to ``[lower, upper]`` and
    the residual is redistributed across unclipped SKUs.  This is the standard
    water-filling solution and guarantees that the tier mass is met exactly.
    """

    w = np.asarray(weights, dtype=float)
    if w.sum() <= 0:
        w = np.ones_like(w)
    w = w / w.sum()
    x = w * total
    for _ in range(max_iter):
        x = np.clip(x, lower, upper)
        diff = total - float(x.sum())
        if abs(diff) < 1e-10:
            break
        if diff > 0:
            free = x < upper - 1e-10
            if not free.any():
                break
            x[free] += diff * (w[free] / w[free].sum())
        else:
            free = x > lower + 1e-10
            if not free.any():
                break
            x[free] += diff * (w[free] / w[free].sum())
    if abs(float(x.sum()) - total) > 1e-6:
        # Numerical last resort: distribute any residual evenly.
        residual = total - float(x.sum())
        x += residual / len(x)
    return x


def _asin(rng: np.random.Generator) -> str:
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "B0" + "".join(rng.choice(list(alphabet), size=8))


def _fnsku(rng: np.random.Generator) -> str:
    return "X00" + "".join(rng.choice(list("0123456789"), size=7))


def _seasonality(dates: Iterable[date]) -> np.ndarray:
    """Calendar seasonality with real promo spikes and weekday effects."""

    month_factor = {
        1: 0.82, 2: 0.85, 3: 0.92, 4: 0.95, 5: 0.98, 6: 1.00,
        7: 1.05, 8: 1.02, 9: 1.00, 10: 1.10, 11: 1.25, 12: 1.32,
    }
    weekday_factor = [1.02, 0.95, 0.97, 1.00, 1.08, 1.06, 0.92]  # Mon..Sun
    promos = {
        date(2025, 11, 28): 2.20,  # Black Friday
        date(2025, 12, 1): 1.90,   # Cyber Monday
        date(2026, 2, 14): 1.25,   # Valentine's Day
        date(2026, 5, 10): 1.15,   # Mother's Day
        date(2026, 7, 14): 1.80,   # Prime Day
        date(2026, 7, 15): 1.75,   # Prime Day
        date(2026, 10, 13): 1.45,  # Prime Big Deal Days
        date(2026, 10, 14): 1.40,
    }
    out = []
    for d in dates:
        f = month_factor[d.month] * weekday_factor[d.weekday()]
        f *= promos.get(d, 1.0)
        out.append(f)
    return np.asarray(out, dtype=float)


def _negative_binomial(rng: np.random.Generator, mean: float, cv: float) -> int:
    """Overdispersed integer draw with a target coefficient of variation."""

    if mean <= 0:
        return 0
    if cv <= 0:
        return int(round(mean))
    var = (cv * mean) ** 2
    # Poisson is the limiting case when the requested CV is at or below
    # the Poisson CV (1/sqrt(mean)).
    if var <= mean * 1.000001:
        return int(rng.poisson(mean))
    n = mean * mean / (var - mean)
    p = n / (n + mean)
    return int(rng.negative_binomial(n, p))


def _largest_remainder(total: int, proportions: list[float]) -> list[int]:
    """Integer allocation with an exact sum and deterministic tie-breaking."""

    if total <= 0:
        return [0] * len(proportions)
    raw = np.asarray(proportions, dtype=float)
    raw = raw / raw.sum() * total
    base = np.floor(raw).astype(int)
    residual = total - int(base.sum())
    order = np.argsort(-(raw - base))
    for i in order[:residual]:
        base[i] += 1
    return base.tolist()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def build_catalog(seed: int = config.SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the 160-child-SKU catalogue and the latent simulation truth table."""

    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for fam in FAMILIES:
        norm = sum(a * b * c for _, a in fam.sizes for _, b in fam.colors for _, c in fam.hardware)
        for size, a_size in fam.sizes:
            for color, a_color in fam.colors:
                for hardware, a_hw in fam.hardware:
                    share = a_size * a_color * a_hw / norm
                    sku = (
                        f"{fam.code}-{fam.material_code}-{SIZE_CODE[size]}-"
                        f"{COLOR_CODE[color]}-{HARDWARE_CODE[hardware]}"
                    )
                    rows.append(
                        {
                            "sku_id": sku,
                            "parent_sku_id": fam.parent_sku_id,
                            "asin": _asin(rng),
                            "fnsku": _fnsku(rng),
                            "shopify_variant_id": 4_400_000_000_000 + len(rows),
                            "material": fam.material,
                            "size_width": size,
                            "color": color,
                            "hardware": hardware,
                            "abc_xyz_tier": "",  # assigned below
                            "z_score": 0.0,
                            "master_carton_qty": fam.master_carton_qty,
                            "carton_volume_cbm": fam.carton_volume_cbm,
                            "carton_weight_kg": fam.carton_weight_kg,
                            "sku_moq_units": fam.sku_moq_units,
                            "po_aggregate_moq_units": config.DEFAULT_PO_AGGREGATE_MOQ_UNITS,
                            "fob_unit_cost": fam.fob_unit_cost,
                            "landed_cost": fam.landed_cost,
                            "retail_price": fam.retail_price,
                            "parent_stationary_share": round(float(share), 6),
                            "unit_volume_cuft": fam.unit_volume_cuft,
                            "fba_channel_share": fam.channel_fba_share,
                            "fba_size_tier": "Small Standard",
                            "supplier_id": fam.supplier_id,
                            "_family_weight": fam.family_weight,
                            "_seasonal_amplitude": fam.seasonal_amplitude,
                        }
                    )

    catalog = pd.DataFrame(rows)
    # Stable order: family, size, colour, hardware as generated.
    catalog = catalog.reset_index(drop=True)

    # ------------------------------------------------------------------
    # ABC-XYZ assignment: the top 30 hero-family SKUs absorb 73.5% of
    # volume.  Rank by family-weighted stationary share, which is the same
    # mechanism that generates the Pareto skew in the attribute matrix.
    # ------------------------------------------------------------------
    catalog["_score"] = catalog["parent_stationary_share"] * catalog["_family_weight"]
    hero_mask = catalog["parent_sku_id"].str.startswith(("P01", "P02", "P03"))
    hero_idx = catalog.index[hero_mask].to_numpy()
    other_idx = catalog.index[~hero_mask].to_numpy()

    hero_order = hero_idx[np.argsort(-catalog.loc[hero_idx, "_score"].to_numpy())]
    ax_idx = hero_order[:18]
    ay_idx = hero_order[18:30]

    remaining = np.array([i for i in catalog.index if i not in set(ax_idx) | set(ay_idx)])
    remaining_order = remaining[np.argsort(-catalog.loc[remaining, "_score"].to_numpy())]
    bx_idx = remaining_order[:30]
    by_idx = remaining_order[30:50]
    cz_idx = remaining_order[50:]

    for tier, idx in (
        ("AX", ax_idx), ("AY", ay_idx), ("BX", bx_idx),
        ("BY", by_idx), ("CZ", cz_idx),
    ):
        catalog.loc[idx, "abc_xyz_tier"] = tier
    catalog["z_score"] = catalog["abc_xyz_tier"].map(config.Z_SCORES)

    # ------------------------------------------------------------------
    # Daily demand means: exact tier mass, bounded within published ranges.
    # ------------------------------------------------------------------
    true_mean = np.zeros(len(catalog))
    cv = np.zeros(len(catalog))
    fba_share = np.zeros(len(catalog))
    for tier, mass in TIER_MASS.items():
        idx = catalog.index[catalog["abc_xyz_tier"] == tier].to_numpy()
        lo, hi = TIER_BOUNDS[tier]
        weights = catalog.loc[idx, "parent_stationary_share"].to_numpy()
        means = _allocate_with_bounds(mass, weights, lo, hi)
        true_mean[idx] = means
        cv_lo, cv_hi = TIER_CV_BOUNDS[tier]
        cv[idx] = rng.uniform(cv_lo, cv_hi, size=len(idx))
        base_share = catalog.loc[idx, "fba_channel_share"].to_numpy()
        fba_share[idx] = np.clip(base_share + rng.normal(0, 0.025, size=len(idx)), 0.35, 0.80)

    catalog["_true_daily_mean"] = true_mean
    catalog["_cv"] = cv
    catalog["_fba_share"] = fba_share

    truth = pd.DataFrame(
        {
            "sku_id": catalog["sku_id"],
            "parent_sku_id": catalog["parent_sku_id"],
            "abc_xyz_tier": catalog["abc_xyz_tier"],
            "true_daily_mean_global": true_mean,
            "target_cv": cv,
            "fba_channel_share": fba_share,
        }
    )
    return catalog, truth


# ---------------------------------------------------------------------------
# Scenario selection
# ---------------------------------------------------------------------------
def _select_scenarios(catalog: pd.DataFrame, truth: pd.DataFrame) -> dict[str, tuple[str, int]]:
    """Choose the nine deliberate fee / censoring stress FNSKUs."""

    merged = truth.merge(
        catalog[["sku_id", "abc_xyz_tier"]].rename(columns={"abc_xyz_tier": "tier"}),
        on="sku_id",
    )
    a = merged[merged["tier"].isin(["AX", "AY"])].sort_values("true_daily_mean_global", ascending=False)
    critical = a.iloc[[0, 3, 6]]["sku_id"].tolist()
    penalty_pool = merged[merged["sku_id"].isin(critical) == False]
    penalty_pool = penalty_pool[penalty_pool["tier"].isin(["AX", "AY", "BX"])]
    penalty_pool = penalty_pool.sort_values("true_daily_mean_global", ascending=False)
    penalty = penalty_pool.iloc[[0, 4, 8, 12]]["sku_id"].tolist()

    c = merged[merged["tier"] == "CZ"].copy()
    c["distance"] = (c["true_daily_mean_global"] - 1.0).abs()
    aged = c.sort_values("distance").iloc[[0, 3]]["sku_id"].tolist()

    scenarios: dict[str, tuple[str, int]] = {}
    for sku, target in zip(critical, (5, 8, 11)):
        scenarios[sku] = ("critical", target)
    for sku, target in zip(penalty, (16, 18, 22, 23)):
        scenarios[sku] = ("penalty", target)
    for sku in aged:
        scenarios[sku] = ("aged", 0)
    return scenarios


# ---------------------------------------------------------------------------
# Inventory simulation
# ---------------------------------------------------------------------------
def _simulate_inventory(
    catalog: pd.DataFrame,
    truth: pd.DataFrame,
    dates: list[date],
    rng: np.random.Generator,
) -> dict[str, object]:
    """Simulate daily channel demand, stockouts, transfers and factory POs."""

    n = len(catalog)
    days = len(dates)
    sku_list = catalog["sku_id"].tolist()
    idx_of = {s: i for i, s in enumerate(sku_list)}
    master_carton = catalog["master_carton_qty"].to_numpy()
    moq = catalog["sku_moq_units"].to_numpy()
    z_score = catalog["z_score"].to_numpy()
    fba_share = truth["fba_channel_share"].to_numpy()
    means = truth["true_daily_mean_global"].to_numpy()
    cvs = truth["target_cv"].to_numpy()
    seasonal = _seasonality(dates)

    # Seasonality amplification by family (P06 laundry bags in Q4/Q1,
    # P05 shoe bags in Q4 gifting, P03 toiletry kits in the summer travel peak).
    fam_amp = np.ones((n, days))
    for i, parent in enumerate(catalog["parent_sku_id"]):
        amp = float(catalog.loc[i, "_seasonal_amplitude"])
        if parent.startswith("P06"):
            fam_amp[i] = np.where(
                np.array([d.month in (11, 12, 1, 2) for d in dates]), amp, 1.0
            )
        elif parent.startswith("P05"):
            fam_amp[i] = np.where(
                np.array([d.month in (11, 12) for d in dates]), amp, 1.0
            )
        elif parent.startswith("P03"):
            fam_amp[i] = np.where(
                np.array([d.month in (6, 7, 8) for d in dates]), amp, 1.0
            )

    global_demand = np.zeros((n, days), dtype=int)
    fba_demand = np.zeros((n, days), dtype=int)
    dtc_demand = np.zeros((n, days), dtype=int)
    for i in range(n):
        lam = means[i] * seasonal * fam_amp[i]
        for t in range(days):
            global_demand[i, t] = _negative_binomial(rng, lam[t], cvs[i])
        fba_demand[i] = rng.binomial(global_demand[i], fba_share[i])
        dtc_demand[i] = global_demand[i] - fba_demand[i]

    scenarios = _select_scenarios(catalog, truth)

    fba_inv = np.maximum(1, np.round(fba_demand.mean(axis=1) * 55)).astype(int)
    tpl_inv = np.maximum(1, np.round(global_demand.mean(axis=1) * 105 + 300)).astype(int)

    pending: list[list[dict]] = [[] for _ in range(n)]
    open_pos: list[list[dict]] = [[] for _ in range(n)]

    opening_fba = np.zeros((n, days), dtype=int)
    closing_fba = np.zeros((n, days), dtype=int)
    opening_dtc = np.zeros((n, days), dtype=int)
    closing_dtc = np.zeros((n, days), dtype=int)
    sold_fba = np.zeros((n, days), dtype=int)
    sold_dtc = np.zeros((n, days), dtype=int)
    fc_transfer = np.zeros((n, days), dtype=int)
    inb_shipped = np.zeros((n, days), dtype=int)
    inb_receiving = np.zeros((n, days), dtype=int)
    fba_available_snap = np.zeros((n, days), dtype=int)
    tpl_onhand_snap = np.zeros((n, days), dtype=int)
    pending_snap = np.zeros((n, days), dtype=int)
    regime = np.full((n, days), "IN_STOCK", dtype=object)

    for t, d in enumerate(dates):
        # ---- receive factory POs ----------------------------------------
        for i in range(n):
            arrived = []
            for po in open_pos[i]:
                if po["arrival_day"] <= t:
                    tpl_inv[i] += int(po["units"])
                    arrived.append(po)
            for po in arrived:
                open_pos[i].remove(po)

        # ---- scenario inventory conditioning ----------------------------
        if t == 275:
            for sku, (kind, target_days) in scenarios.items():
                i = idx_of[sku]
                if kind in ("critical", "penalty"):
                    # Cancel any transfer already on the water and return it
                    # to the 3PL before resetting the FBA position.
                    returned = sum(p["qty"] for p in pending[i])
                    tpl_inv[i] += returned
                    pending[i] = []
                    desired = int(round(target_days * max(fba_demand[i, t], 1)))
                    if fba_inv[i] > desired:
                        excess = int(fba_inv[i] - desired)
                        fba_inv[i] = desired
                        tpl_inv[i] += excess
                elif kind == "aged":
                    # Legacy overstock that never cleared: inject a discrete
                    # aged lot so the 15th-of-month cliff audit has material.
                    fba_inv[i] += 180
                    fc_transfer[i, t] += 180

        if t >= 275:
            for sku, (kind, target_days) in scenarios.items():
                if kind == "critical" and t >= 350:
                    continue
                if kind not in ("critical", "penalty"):
                    continue
                i = idx_of[sku]
                desired = int(round(target_days * max(float(fba_demand[i, t]), 1.0)))
                if fba_inv[i] < desired:
                    add = desired - int(fba_inv[i])
                    fba_inv[i] += add
                    fc_transfer[i, t] += add

        # ---- transfer arrivals -----------------------------------------
        for i in range(n):
            arrived = []
            for tr in pending[i]:
                if tr["arrival_day"] <= t:
                    fba_inv[i] += int(tr["qty"])
                    arrived.append(tr)
            for tr in arrived:
                pending[i].remove(tr)

        # ---- daily opening, sales and closing ---------------------------
        for i in range(n):
            op_f = int(fba_inv[i])
            op_d = max(0, int(tpl_inv[i]))
            opening_fba[i, t] = op_f
            opening_dtc[i, t] = op_d
            s_f = min(int(fba_demand[i, t]), op_f)
            s_d = min(int(dtc_demand[i, t]), op_d)
            sold_fba[i, t] = s_f
            sold_dtc[i, t] = s_d
            fba_inv[i] = op_f - s_f
            tpl_inv[i] = op_d - s_d
            closing_fba[i, t] = int(fba_inv[i])
            closing_dtc[i, t] = int(tpl_inv[i])
            if op_f <= 0:
                regime[i, t] = "FULL_OOS"
            elif fba_inv[i] <= 0:
                regime[i, t] = "PARTIAL_OOS"
            else:
                regime[i, t] = "IN_STOCK"

        # ---- Monday replenishment review --------------------------------
        if d.weekday() == 0:
            for i in range(n):
                sku = sku_list[i]
                if sku in scenarios and t >= 260:
                    continue
                hist_start = max(0, t - 28)
                fba_hist = sold_fba[i, hist_start:t]
                d_fba_est = float(fba_hist.mean()) if len(fba_hist) else 0.0
                gl_hist = sold_fba[i, max(0, t - 90):t] + sold_dtc[i, max(0, t - 90):t]
                d_glob_est = float(gl_hist.mean()) if len(gl_hist) else 0.0
                sig_glob_est = float(gl_hist.std(ddof=1)) if len(gl_hist) > 2 else max(0.5, 0.3 * d_glob_est)
                if d_fba_est <= 0.05:
                    continue

                ip_fba = fba_inv[i] + sum(p["qty"] for p in pending[i])
                rop1 = (config.L1_MEAN_DAYS + config.FBA_SHIELD_DAYS) * d_fba_est
                if ip_fba <= rop1:
                    target = (
                        config.L1_MEAN_DAYS + config.FBA_REVIEW_CYCLE_DAYS
                        + config.FBA_SHIELD_DAYS
                    ) * d_fba_est
                    raw = max(0.0, target - ip_fba)
                    cartons = int(np.ceil(raw / master_carton[i]))
                    if ip_fba + cartons * master_carton[i] > config.FBA_AGE_CAP_DAYS * d_fba_est:
                        cartons = int(max(0.0, (config.FBA_AGE_CAP_DAYS * d_fba_est - ip_fba) / master_carton[i]))
                    available_cartons = int(tpl_inv[i] // master_carton[i])
                    ship = max(0, min(cartons, available_cartons))
                    if ship > 0:
                        qty = ship * int(master_carton[i])
                        tpl_inv[i] -= qty
                        lead = int(np.clip(round(rng.normal(config.L1_MEAN_DAYS, config.L1_STD_DAYS)), 7, 26))
                        arrival = t + lead
                        pending[i].append(
                            {
                                "ship_day": t,
                                "arrival_day": arrival,
                                "receive_start": max(t + 1, arrival - 4),
                                "qty": qty,
                            }
                        )

                # Factory PO review
                open_units = sum(p["units"] for p in open_pos[i])
                ip_sys = fba_inv[i] + sum(p["qty"] for p in pending[i]) + tpl_inv[i] + open_units
                shield = config.FBA_SHIELD_DAYS * d_fba_est if 7 * d_fba_est >= 20 else 0.0
                ss2 = z_score[i] * np.sqrt(
                    config.L2_MEAN_DAYS * sig_glob_est ** 2
                    + d_glob_est ** 2 * config.L2_STD_DAYS ** 2
                )
                rop2 = config.L2_MEAN_DAYS * d_glob_est + ss2 + shield
                if ip_sys <= rop2 and open_units < 45 * max(d_glob_est, 0.1):
                    target = (config.L2_MEAN_DAYS + 60.0) * d_glob_est + ss2 + shield
                    raw = max(target - ip_sys, moq[i])
                    units = int(np.ceil(raw / master_carton[i]) * master_carton[i])
                    max_units = int(max(moq[i], np.floor((config.SYSTEM_DOS_CAP_DAYS * max(d_glob_est, 0.1) - ip_sys) / master_carton[i]) * master_carton[i]))
                    units = max(moq[i], min(units, max_units))
                    if units > 0:
                        lead = int(np.clip(round(rng.normal(config.L2_MEAN_DAYS, config.L2_STD_DAYS)), 52, 105))
                        open_pos[i].append(
                            {
                                "issue_day": t,
                                "arrival_day": t + lead,
                                "cartons": units // int(master_carton[i]),
                                "units": units,
                            }
                        )

        # ---- daily snapshots -------------------------------------------
        for i in range(n):
            fba_available_snap[i, t] = int(fba_inv[i])
            tpl_onhand_snap[i, t] = max(0, int(tpl_inv[i]))
            ps = sum(int(p["qty"]) for p in pending[i])
            pending_snap[i, t] = ps
            shipped = sum(
                int(p["qty"]) for p in pending[i] if t < p["receive_start"]
            )
            receiving = sum(
                int(p["qty"])
                for p in pending[i]
                if p["receive_start"] <= t < p["arrival_day"]
            )
            inb_shipped[i, t] = shipped
            inb_receiving[i, t] = receiving

    return {
        "dates": dates,
        "sku_list": sku_list,
        "idx_of": idx_of,
        "fba_demand": fba_demand,
        "dtc_demand": dtc_demand,
        "global_demand": global_demand,
        "opening_fba": opening_fba,
        "closing_fba": closing_fba,
        "opening_dtc": opening_dtc,
        "closing_dtc": closing_dtc,
        "sold_fba": sold_fba,
        "sold_dtc": sold_dtc,
        "fc_transfer": fc_transfer,
        "inb_shipped": inb_shipped,
        "inb_receiving": inb_receiving,
        "fba_available": fba_available_snap,
        "tpl_onhand": tpl_onhand_snap,
        "pending": pending_snap,
        "regime": regime,
        "open_pos": open_pos,
        "scenarios": scenarios,
        "master_carton": master_carton,
    }


# ---------------------------------------------------------------------------
# Derived tables
# ---------------------------------------------------------------------------
def _rolling_mean_2d(a: np.ndarray, window: int) -> np.ndarray:
    out = np.empty_like(a, dtype=float)
    for i in range(a.shape[0]):
        s = pd.Series(a[i], dtype=float)
        out[i] = s.rolling(window, min_periods=1).mean().to_numpy()
    return out


def _compute_tau(
    opening: np.ndarray,
    closing: np.ndarray,
    sales: np.ndarray,
) -> np.ndarray:
    """Continuous exposure weight, applied only to partial-day stockouts."""

    n, days = sales.shape
    tau = np.ones((n, days), dtype=float)
    # Prior 30-day mean / std, shifted one day so the weight is causal.
    prior_mean = np.zeros_like(sales, dtype=float)
    prior_std = np.zeros_like(sales, dtype=float)
    for i in range(n):
        s = pd.Series(sales[i], dtype=float)
        prior_mean[i] = s.shift(1).rolling(30, min_periods=3).mean().fillna(s.mean()).to_numpy()
        prior_std[i] = s.shift(1).rolling(30, min_periods=3).std(ddof=1).fillna(max(0.1, s.std(ddof=1))).to_numpy()
        prior_std[i] = np.maximum(prior_std[i], 0.1)
    for i in range(n):
        for t in range(days):
            if opening[i, t] <= 0:
                tau[i, t] = 0.0
            elif closing[i, t] > 0:
                tau[i, t] = 1.0
            else:
                denom = max(float(sales[i, t]), prior_mean[i, t]) + 0.5 * prior_std[i, t]
                tau[i, t] = min(1.0, float(sales[i, t]) / denom) if denom > 0 else 0.0
    return np.clip(tau, 0.0, 1.0)


def _fba_snapshot(
    catalog: pd.DataFrame,
    sim: dict[str, object],
    rng: np.random.Generator,
    snapshot_date: date,
) -> pd.DataFrame:
    n = len(catalog)
    closing = sim["closing_fba"]
    sold = sim["sold_fba"]
    days = closing.shape[1]
    avail = closing[:, -1].astype(int)

    hdos30 = np.zeros(n)
    hdos90 = np.zeros(n)
    for i in range(n):
        inv30 = float(np.mean(closing[i, max(0, days - 30):]))
        s30 = float(np.mean(sold[i, max(0, days - 30):]))
        inv90 = float(np.mean(closing[i, max(0, days - 90):]))
        s90 = float(np.mean(sold[i, max(0, days - 90):]))
        hdos30[i] = inv30 / max(s30, 0.10)
        hdos90[i] = inv90 / max(s90, 0.10)

    weekly = sold[:, max(0, days - 7):].sum(axis=1)
    scenarios = sim["scenarios"]
    sku_list = sim["sku_list"]
    idx_of = sim["idx_of"]

    # ------------------------------------------------------------------
    # Age-bucket profiles.  Aged-cliff scenarios get a deliberately old mix;
    # a few pre-cliff SKUs carry 181-270d stock for the Monday markdown list.
    # ------------------------------------------------------------------
    c_idx = [i for i, s in enumerate(sku_list) if catalog.iloc[i]["abc_xyz_tier"] == "CZ"]
    precliff_extra = c_idx[10:13] if len(c_idx) >= 13 else c_idx[:3]
    rows = []
    for i, sku in enumerate(sku_list):
        total = int(avail[i])
        scenario_kind = scenarios.get(sku, (None, 0))[0]
        if scenario_kind == "aged":
            props = [0.05, 0.15, 0.20, 0.35, 0.25]
        elif i in precliff_extra:
            props = [0.50, 0.20, 0.30, 0.00, 0.00]
        elif catalog.iloc[i]["abc_xyz_tier"] in ("AX", "AY"):
            props = [0.64, 0.30, 0.06, 0.00, 0.00]
        elif catalog.iloc[i]["abc_xyz_tier"] in ("BX", "BY"):
            props = [0.58, 0.30, 0.12, 0.00, 0.00]
        else:
            props = [0.50, 0.30, 0.20, 0.00, 0.00]
        buckets = _largest_remainder(total, props)
        rows.append(
            {
                "snapshot_date": snapshot_date.isoformat(),
                "sku_id": sku,
                "fnsku": catalog.iloc[i]["fnsku"],
                "fba_available": total,
                "fba_fc_transfer": int(sim["fc_transfer"][i, -1]),
                "fba_inbound_shipped": int(sim["inb_shipped"][i, -1]),
                "fba_inbound_receiving": int(sim["inb_receiving"][i, -1]),
                "short_term_hdos_30d": round(float(hdos30[i]), 2),
                "long_term_hdos_90d": round(float(hdos90[i]), 2),
                "inv_age_0_90": buckets[0],
                "inv_age_91_180": buckets[1],
                "inv_age_181_270": buckets[2],
                "inv_age_271_365": buckets[3],
                "inv_age_365_plus": buckets[4],
                "weekly_units_sold_7d": int(weekly[i]),
                "low_inv_fee_exempt_flag": bool(weekly[i] < config.LOW_INVENTORY_FEE_MIN_WEEKLY_UNITS),
            }
        )
    return pd.DataFrame(rows)


def _daily_history(catalog: pd.DataFrame, sim: dict[str, object]) -> pd.DataFrame:
    dates = sim["dates"]
    frames = []
    tau_f = _compute_tau(sim["opening_fba"], sim["closing_fba"], sim["sold_fba"])
    tau_d = _compute_tau(sim["opening_dtc"], sim["closing_dtc"], sim["sold_dtc"])
    for i, sku in enumerate(sim["sku_list"]):
        for t, d in enumerate(dates):
            for channel, op, cl, sold, tau, regime in (
                ("AMZ_US", sim["opening_fba"][i, t], sim["closing_fba"][i, t],
                 sim["sold_fba"][i, t], tau_f[i, t], sim["regime"][i, t]),
                ("SHOPIFY_DTC", sim["opening_dtc"][i, t], sim["closing_dtc"][i, t],
                 sim["sold_dtc"][i, t], tau_d[i, t],
                 "IN_STOCK" if sim["opening_dtc"][i, t] > 0 and sim["closing_dtc"][i, t] > 0
                 else ("FULL_OOS" if sim["opening_dtc"][i, t] <= 0 else "PARTIAL_OOS")),
            ):
                frames.append(
                    {
                        "activity_date": d.isoformat(),
                        "sku_id": sku,
                        "channel": channel,
                        "opening_inv": int(op),
                        "closing_inv": int(cl),
                        "observed_sales": int(sold),
                        "in_stock_flag": int(op > 0 and cl > 0),
                        "effective_active_day_weight": round(float(tau), 4),
                        "stockout_regime_flag": regime,
                    }
                )
    return pd.DataFrame(frames)


def _shopify_orders(
    catalog: pd.DataFrame,
    sim: dict[str, object],
    rng: np.random.Generator,
) -> pd.DataFrame:
    rows = []
    seq = 0
    price = catalog["retail_price"].to_numpy()
    dates = sim["dates"]
    for i, sku in enumerate(sim["sku_list"]):
        for t, d in enumerate(dates):
            remaining = int(sim["sold_dtc"][i, t])
            while remaining > 0:
                qty = int(min(remaining, rng.integers(1, 4)))
                remaining -= qty
                seq += 1
                discount = 0.0
                if rng.random() < 0.18:
                    discount = round(float(price[i]) * 0.05, 2)
                elif d.month in (11, 12) and rng.random() < 0.10:
                    discount = round(float(price[i]) * 0.10, 2)
                ts = datetime.combine(d, datetime.min.time()) + timedelta(
                    seconds=int(rng.integers(0, 86_400))
                )
                rows.append(
                    {
                        "order_line_id": f"SHP-{seq:07d}",
                        "timestamp": ts.isoformat(sep=" "),
                        "sku_id": sku,
                        "quantity": qty,
                        "gross_price": float(price[i]),
                        "discount": discount,
                        "fulfillment_status": "fulfilled" if rng.random() > 0.02 else "unfulfilled",
                    }
                )
    return pd.DataFrame(rows)


def _tpl_stock(catalog: pd.DataFrame, sim: dict[str, object], snapshot_date: date) -> pd.DataFrame:
    rows = []
    for i, sku in enumerate(sim["sku_list"]):
        on_hand = int(sim["tpl_onhand"][i, -1])
        m = int(catalog.iloc[i]["master_carton_qty"])
        sealed = on_hand // m
        loose = on_hand - sealed * m
        rows.append(
            {
                "snapshot_date": snapshot_date.isoformat(),
                "sku_id": sku,
                "on_hand_units": on_hand,
                "sealed_master_cartons": sealed,
                "loose_pick_units": loose,
                "allocated_dtc_units": 0,
                "pending_fba_transfer_units": int(sim["pending"][i, -1]),
            }
        )
    return pd.DataFrame(rows)


def _open_pos_table(
    catalog: pd.DataFrame,
    sim: dict[str, object],
    snapshot_date: date,
) -> pd.DataFrame:
    rows = []
    line_no = 0
    po_seq = 0
    for i, sku in enumerate(sim["sku_list"]):
        for po in sim["open_pos"][i]:
            if po["arrival_day"] <= len(sim["dates"]) - 1:
                continue
            line_no += 1
            po_seq += 1
            cartons = int(po["cartons"])
            cbm = cartons * float(catalog.iloc[i]["carton_volume_cbm"])
            if cartons >= 600:
                mode = "FCL_40HC"
            elif cbm >= config.LCL_FCL_BREAKEVEN_CBM:
                mode = "FCL_20GP"
            else:
                mode = "LCL_OCEAN"
            eta = snapshot_date + timedelta(days=int(po["arrival_day"] - (len(sim["dates"]) - 1)))
            issue = snapshot_date - timedelta(days=int((len(sim["dates"]) - 1) - po["issue_day"]))
            if po["issue_day"] > len(sim["dates"]) - 15:
                status = "IN_PRODUCTION"
            elif po["arrival_day"] - (len(sim["dates"]) - 1) <= 7:
                status = "CUSTOMS_HOLD"
            else:
                status = "ON_WATER"
            rows.append(
                {
                    "po_line_id": f"PO-2026-{po_seq:03d}-L01",
                    "po_number": f"PO-2026-{po_seq:03d}",
                    "sku_id": sku,
                    "supplier_id": catalog.iloc[i]["supplier_id"],
                    "po_issue_date": issue.isoformat(),
                    "expected_3pl_eta": eta.isoformat(),
                    "ordered_cartons": cartons,
                    "ordered_units": int(po["units"]),
                    "open_units": int(po["units"]),
                    "status": status,
                    "freight_mode": mode,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_all(
    output_dir: Path | None = None,
    seed: int = config.SEED,
    snapshot_date: date = config.SNAPSHOT_DATE,
    processed_dir: Path | None = None,
) -> dict[str, Path]:
    """Generate the six relational feeds and return their paths.

    ``processed_dir`` receives the latent ``generation_truth.csv`` audit table.
    Tests pass an isolated directory here so that generation never mutates the
    repository's processed outputs.  Production callers can omit it and keep
    the documented ``data/processed`` location.
    """

    out = Path(output_dir) if output_dir else config.DATA_RAW_DIR
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    catalog, truth = build_catalog(seed=seed)
    dates = [snapshot_date - timedelta(days=config.HISTORY_DAYS - 1 - i) for i in range(config.HISTORY_DAYS)]
    sim = _simulate_inventory(catalog, truth, dates, rng)

    fba = _fba_snapshot(catalog, sim, rng, snapshot_date)
    daily = _daily_history(catalog, sim)
    shopify = _shopify_orders(catalog, sim, rng)
    tpl = _tpl_stock(catalog, sim, snapshot_date)
    pos = _open_pos_table(catalog, sim, snapshot_date)

    # Scenario flags are useful for validation, but never leak into the
    # production-shaped catalog or transactional feeds.
    truth = truth.copy()
    truth["scenario_kind"] = truth["sku_id"].map(
        {k: v[0] for k, v in sim["scenarios"].items()}
    ).fillna("none")
    truth["scenario_target_days"] = truth["sku_id"].map(
        {k: v[1] for k, v in sim["scenarios"].items()}
    ).fillna(0).astype(int)

    catalog_out = catalog[CATALOG_COLUMNS].copy()
    files = {
        "catalog_master": out / "catalog_master.csv",
        "amazon_fba_inventory": out / "amazon_fba_inventory.csv",
        "shopify_sales_report": out / "shopify_sales_report.csv",
        "warehouse_3pl_stock": out / "warehouse_3pl_stock.csv",
        "daily_sales_history": out / "daily_sales_history.csv",
        "open_purchase_orders": out / "open_purchase_orders.csv",
    }
    catalog_out.to_csv(files["catalog_master"], index=False)
    fba.to_csv(files["amazon_fba_inventory"], index=False)
    shopify.to_csv(files["shopify_sales_report"], index=False)
    tpl.to_csv(files["warehouse_3pl_stock"], index=False)
    daily.to_csv(files["daily_sales_history"], index=False)
    pos.to_csv(files["open_purchase_orders"], index=False)

    processed = Path(processed_dir) if processed_dir else config.DATA_PROCESSED_DIR
    processed.mkdir(parents=True, exist_ok=True)
    truth_path = processed / "generation_truth.csv"
    truth.to_csv(truth_path, index=False)
    files["generation_truth"] = truth_path
    return files


if __name__ == "__main__":
    result = generate_all()
    for key, path in result.items():
        print(f"{key:28s} -> {path}")
