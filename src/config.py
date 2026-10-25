"""Central policy and micro-economic configuration.

All constants in this module are traced to the 2026 Amazon FBA fee schedule,
the two-echelon lead-time profile, or the physical master-carton / ocean
container constraints documented in the project's internal research dossier (kept private).

Nothing here is a placeholder: every fee, lead time, carton specification and
service-level factor is used by at least one engine module or Excel formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Repository paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DOCS_DIR = PROJECT_ROOT / "docs"

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
SEED = 42
HISTORY_DAYS = 365
# The weekly control-tower snapshot is taken on the last Sunday of the
# simulated history.  Keeping the date fixed makes every artifact reproducible.
SNAPSHOT_DATE = date(2026, 10, 25)
N_SKUS = 160

# ---------------------------------------------------------------------------
# Amazon FBA 2026 Low-Inventory-Level Fee (US, Small Standard <= 16 oz)
# The fee is assessed at the seller-FNSKU level when *both* the 30-day and
# 90-day Historical Days of Supply are below 28 days.
# ---------------------------------------------------------------------------
LOW_INVENTORY_FEE_US: dict[str, float] = {
    "HDoS_21_28": 0.32,
    "HDoS_14_21": 0.63,
    "HDoS_0_14": 0.89,
}
LOW_INVENTORY_FEE_THRESHOLD_DAYS = 28.0
LOW_INVENTORY_FEE_MIN_WEEKLY_UNITS = 20


def low_inventory_fee_per_unit(
    effective_hdos: float,
    weekly_units: float,
    exempt: bool = False,
) -> float:
    """Return the 2026 per-unit Low-Inventory-Level surcharge.

    ``effective_hdos`` must be ``max(HDoS_30, HDoS_90)``.  The fee only applies
    when *both* windows are below 28 days, which is exactly equivalent to the
    maximum being below 28.  Sellers moving fewer than 20 units in the trailing
    seven days are exempt under the published programme rules.
    """

    if exempt or weekly_units < LOW_INVENTORY_FEE_MIN_WEEKLY_UNITS:
        return 0.0
    if effective_hdos >= LOW_INVENTORY_FEE_THRESHOLD_DAYS:
        return 0.0
    if effective_hdos < 14.0:
        return LOW_INVENTORY_FEE_US["HDoS_0_14"]
    if effective_hdos < 21.0:
        return LOW_INVENTORY_FEE_US["HDoS_14_21"]
    return LOW_INVENTORY_FEE_US["HDoS_21_28"]


# ---------------------------------------------------------------------------
# Amazon FBA 2026 Aged Inventory Surcharge (US, standard-size)
# Eight tiers, assessed monthly from the 15th-of-month FIFO snapshot.
# rate_usd_per_cuft_month and optional per-unit monthly floor.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgedInventoryTier:
    age_min: int
    age_max: int
    label: str
    rate_usd_per_cuft_month: float
    unit_floor_usd: float | None = None


AGED_INVENTORY_TIERS: tuple[AgedInventoryTier, ...] = (
    AgedInventoryTier(0, 180, "0-180", 0.00),
    AgedInventoryTier(181, 210, "181-210", 0.50),
    AgedInventoryTier(211, 240, "211-240", 1.00),
    AgedInventoryTier(241, 270, "241-270", 1.50),
    AgedInventoryTier(271, 300, "271-300", 5.45),
    AgedInventoryTier(301, 330, "301-330", 5.70),
    AgedInventoryTier(331, 365, "331-365", 5.90),
    AgedInventoryTier(366, 455, "366-455", 6.90, 0.30),
    AgedInventoryTier(456, 10_000, "456+", 7.90, 0.35),
)

REMOVAL_FEE_PER_UNIT_USD = 0.84
AIS_PAYBACK_MONTHS = 12.0

# Aggregated rates used for the five age buckets exported by Seller Central.
# 181-270 is the arithmetic mid-point of the three pre-cliff tiers; 271-365
# uses the entry-cliff rate (5.45) so the audit never overstates the benefit
# of waiting; 366+ uses the 6.90 rate with its 0.30/unit floor.
AGE_BUCKET_VOL_RATES: dict[str, float] = {
    "0_90": 0.00,
    "91_180": 0.00,
    "181_270": 1.00,
    "271_365": 5.45,
    "365_plus": 6.90,
}
AGE_BUCKET_UNIT_FLOORS: dict[str, float] = {
    "365_plus": 0.30,
}


def aged_inventory_monthly_surcharge(
    units_by_bucket: dict[str, float],
    unit_volume_cuft: float,
) -> float:
    """Project the next monthly Aged Inventory Surcharge for one FNSKU.

    Volumetric and per-unit floors are combined with ``max`` exactly as Amazon
    bills the 366+ and 456+ tiers.  The five exported buckets are sufficient
    for a conservative projection; the 15th-of-month ledger remains the source
    of truth for the final invoice.
    """

    total = 0.0
    for bucket, units in units_by_bucket.items():
        if units <= 0:
            continue
        rate = AGE_BUCKET_VOL_RATES.get(bucket, 0.0)
        vol_charge = units * unit_volume_cuft * rate
        floor = AGE_BUCKET_UNIT_FLOORS.get(bucket)
        if floor is not None:
            vol_charge = max(vol_charge, units * floor)
        total += vol_charge
    return total


# ---------------------------------------------------------------------------
# Two-echelon lead-time profile
# ---------------------------------------------------------------------------
L1_MEAN_DAYS = 14.0       # 3PL -> FBA: pick, LTL, FC dock-to-stock
L1_STD_DAYS = 3.0
L2_MEAN_DAYS = 75.0       # Factory -> 3PL: production, ocean, customs, putaway
L2_STD_DAYS = 10.0

# Policy constants
KAPPA_CREDIBILITY = 7.0
GAMMA_CANNIBALIZATION = 0.10
FBA_SHIELD_DAYS = 32.0          # 28-day fee threshold + 4-day operational cushion
FBA_REVIEW_CYCLE_DAYS = 21.0    # 3-week transfer cadence
FBA_AGE_CAP_DAYS = 120.0        # hard cap on post-transfer FBA days of supply
SYSTEM_DOS_CAP_DAYS = 135.0     # hard cap for factory / knapsack allocations
FBA_LOW_INVENTORY_TRIGGER_DAYS = 28.0
HERO_WEEKLY_UNIT_THRESHOLD = 20.0

# Service levels by ABC-XYZ tier
Z_SCORES: dict[str, float] = {
    "AX": 2.054,
    "AY": 1.881,
    "BX": 1.645,
    "BY": 1.645,
    "CZ": 1.282,
}
SERVICE_LEVELS: dict[str, float] = {
    "AX": 0.98,
    "AY": 0.97,
    "BX": 0.95,
    "BY": 0.95,
    "CZ": 0.90,
}

# ---------------------------------------------------------------------------
# Ocean container capacities (usable floor-loaded CBM)
# ---------------------------------------------------------------------------
CONTAINER_20GP_CBM = 28.8
CONTAINER_40HC_CBM = 70.0
LCL_FCL_BREAKEVEN_CBM = 15.0
# Reserved volumetric headroom for the Tier-A profit-velocity top-up when a
# weekly order would otherwise consume the whole 20' GP.
TOPUP_RESERVE_CBM = 1.8

# Factory commercial constraints
DEFAULT_SKU_MOQ_UNITS = 300
DEFAULT_PO_AGGREGATE_MOQ_UNITS = 5_000

# Unit economics used for marginal profit velocity in the knapsack
AMAZON_REFERRAL_RATE = 0.15
DTC_WELCOME_DISCOUNT = 0.05
DTC_PAYMENT_FEE_RATE = 0.029
DTC_PAYMENT_FIXED_FEE = 0.30
