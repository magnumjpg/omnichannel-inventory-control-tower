# Weekly Omnichannel Inventory Briefing

**Snapshot:** 2026-10-25  
**Generated:** 2026-10-26 10:51  
**Scope:** 160 child FNSKUs · 6 product families · Amazon FBA US + Shopify DTC + domestic 3PL + Shenzhen/Ningbo factory

---

## 1. Executive KPI snapshot

| KPI | Value | Policy / interpretation |
| :--- | ---: | :--- |
| FBA available | 19,980 u | Physical sellable stock |
| 3PL on hand | 35,827 u | Domestic upstream buffer |
| Open factory POs | 50,500 u | In production / on water |
| FNSKUs HDoS < 14d | 53 | Critical stockout band; exempt long-tail included |
| Non-exempt fee-band FNSKUs | 8 | 30-day window 14-28d; charge model uses max(30d, 90d) |
| 3PL → FBA transfer | 11,450 u / 147 cartons | Age-safe master-carton multiples |
| Factory PO | 38,750 u / 461 cartons | Aggregate MOQ MET |
| Container | 28.770 CBM | FCL_20GP · 99.9% utilisation (base 26.910 + top-up 1.860) |
| Worst-case 30-day fee exposure | $3,442.00 | Upper bound: every transferred unit ships while still inside the band |
| Projected monthly AIS | $38.63 | 2026 aged-inventory surcharge |
| Removal recommendations | 2 | Payback ≤ 12 months |

## 2. Demand unconstraining

The estimator widens the exposure window until the SKU becomes visible: the active-day rate when τ > 0, the 90-day rate when the SKU was dark for the whole month, and the lifetime active-day rate when even 90 days were dark. The sister prior with its 10% substitution deflator is a last resort. The nine-cutoff walk-forward backtest behind that ladder, including the rejected κ-blend, is in `outputs/estimator_backtest.md`. The table shows the five largest global unconstrained velocities.

| SKU | Tier | Naive 30d | Active-day | Cross-sectional | Unconstrained FBA | DTC | Global | Uplift |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `P01-RPN-L-BLK-ZIP` | AX | 36.00 | 42.92 | 29.97 | 42.92 | 14.23 | 57.15 | 19.2% |
| `P02-TPU-M-BLK-ZIP` | AX | 40.87 | 40.87 | 40.87 | 40.87 | 14.63 | 55.50 | 0.0% |
| `P03-RPX-M-BLK-ZIP` | AX | 30.90 | 30.90 | 30.90 | 30.90 | 24.07 | 54.97 | 0.0% |
| `P01-RPN-M-NVY-ZIP` | AX | 34.20 | 36.45 | 30.30 | 36.45 | 14.73 | 51.19 | 6.6% |
| `P02-TPU-L-BLK-ZIP` | AX | 12.30 | 36.33 | 29.20 | 36.33 | 13.53 | 49.86 | 195.3% |

27 of 160 FNSKUs show positive uplift; the median uplift among them is 54.8% and the catalogue-wide mean (zero-uplift SKUs included) is 47.5%.

## 3. Nivel 1 · 3PL → FBA transfer

Every transfer is an integer master-carton multiple and respects the 120-day post-transfer FBA age cap. The policy buffer is B_FBA = max(SS₁, 32·D_FBA) for non-exempt heroes.

| SKU | Tier | HDoS | Fee/u | Transfer cartons | Transfer units | Post-DOS | Fee exposure |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `P02-TPU-M-BLK-ZIP` | AX | 21.1 | $0.32 | 21 | 2,100 | 69.3 | $672.00 |
| `P01-RPN-M-NVY-ZIP` | AX | 11.7 | $0.89 | 18 | 1,800 | 49.4 | $1,602.00 |
| `P03-RPX-M-NVY-ZIP` | AX | 23.8 | $0.32 | 31 | 1,550 | 65.0 | $496.00 |
| `P03-RPX-L-BLK-ZIP` | AX | 8.1 | $0.00 | 23 | 1,150 | 41.8 | $0.00 |
| `P01-RPN-M-OLV-ZIP` | AX | 27.3 | $0.32 | 8 | 800 | 68.2 | $256.00 |
| `P02-TPU-M-NVY-ZIP` | AX | 23.2 | $0.32 | 7 | 700 | 46.0 | $224.00 |
| `P02-TPU-L-OLV-ZIP` | AY | 30.2 | $0.00 | 7 | 700 | 61.1 | $0.00 |
| `P01-RPN-M-BLK-ZIP` | AX | 4.4 | $0.00 | 6 | 600 | 18.6 | $0.00 |

**Low-inventory fee watch list**

| SKU | Tier | Effective HDoS | Weekly units | Fee/u | Exposure |
| :--- | :--- | ---: | ---: | ---: | ---: |
| `P01-RPN-M-NVY-ZIP` | AX | 11.7 | 204 | $0.89 | $1,602.00 |
| `P02-TPU-M-BLK-ZIP` | AX | 21.1 | 253 | $0.32 | $672.00 |
| `P03-RPX-M-NVY-ZIP` | AX | 23.8 | 186 | $0.32 | $496.00 |
| `P01-RPN-M-OLV-ZIP` | AX | 27.3 | 139 | $0.32 | $256.00 |
| `P02-TPU-M-NVY-ZIP` | AX | 23.2 | 202 | $0.32 | $224.00 |
| `P01-RPN-L-NVY-ZIP` | AX | 25.2 | 186 | $0.32 | $192.00 |
| `P03-RPX-M-BLK-ZIP` | AX | 26.2 | 243 | $0.32 | $0.00 |
| `P01-RPN-M-SLT-ZIP` | AX | 22.9 | 59 | $0.32 | $0.00 |

## 4. Nivel 2 · Factory PO and container

The weekly cadence books one **FCL_20GP** with a usable capacity of 28.8 CBM. Baseline demand consumes 26.910 CBM; the Tier-A profit-velocity knapsack adds 1.860 CBM for a total of **28.770 CBM** (99.9% fill).

| SKU | Tier | Base units | Top-up cartons | Total cartons | Total CBM | Status |
| :--- | :--- | ---: | ---: | ---: | ---: | :--- |
| `P03-RPX-M-BLK-ZIP` | AX | 7,350 | 0 | 147 | 8.820 | BASE LOAD |
| `P01-RPN-L-BLK-ZIP` | AX | 6,700 | 0 | 67 | 4.020 | BASE LOAD |
| `P01-RPN-M-BLK-WRZ` | AX | 4,300 | 0 | 43 | 2.580 | BASE LOAD |
| `P02-TPU-M-BLK-WRZ` | AX | 4,100 | 0 | 41 | 2.460 | BASE LOAD |
| `P02-TPU-M-OLV-ZIP` | AX | 3,800 | 0 | 38 | 2.280 | BASE LOAD |
| `P01-RPN-M-SLT-ZIP` | AX | 3,600 | 0 | 36 | 2.160 | BASE LOAD |
| `P01-RPN-M-NVY-WRZ` | AX | 3,300 | 0 | 33 | 1.980 | BASE LOAD |
| `P02-TPU-M-BLK-ZIP` | AX | 0 | 31 | 31 | 1.860 | BASE + TOP-UP |
| `P01-RPN-S-BLK-ZIP` | AY | 2,400 | 0 | 24 | 1.440 | BASE LOAD |
| `P04-FLT-CMP-BLK-ZIP` | BX | 900 | 0 | 9 | 0.270 | BASE LOAD |

SKU MOQs and the 5,000-unit aggregate PO floor are hard constraints. Lines whose MOQ would breach the 135-day system cap are surfaced as `MOQ OVERRIDE` in Tab 04 rather than silently over-ordering.

## 5. Nivel 0 · Aged-inventory cliff audit

The following FNSKUs should be removed or liquidated **before the 14th at 23:59 PT** so that the 15th-of-month Amazon aged snapshot does not bill the next tier.

| SKU | Tier | 271d+ units | Monthly AIS | Removal cost | Payback (months) |
| :--- | :--- | ---: | ---: | ---: | ---: |
| `P05-WAX-LRG-SND-ZIP` | CZ | 101 | $16.87 | $113.40 | 6.7 |
| `P02-TPU-L-SLT-WRZ` | CZ | 72 | $10.43 | $80.64 | 7.7 |

## 6. Monday action list

1. **08:00** - Refresh the six feeds, run `python main.py --run-all`, and verify the ingestion integrity gate (zero orphan FNSKUs).
2. **08:30** - Approve Tab 02 exception list; override the unconstrained velocity only where a documented promotion or influencer event explains the residual.
3. **09:00** - Release the 3PL transfer manifest from Tab 03 to the WMS; every line must show `CARTON OK` and `Post-transfer DOS ≤ 120`.
4. **10:00** - Transmit the factory PO from Tab 04; confirm the 20' GP allocation and the Tier-A top-up. Escalate any `MOQ OVERRIDE` line to procurement.
5. **Before the 14th** - Execute removal orders for Tab 05 `EXECUTE REMOVAL` lines and apply the 25% markdown to `DISCOUNT 25% NOW` lines.
6. **Sign-off** - Record the S&OP decision in Tab 00 and archive the workbook hash with the weekly operations log.

---

Generated deterministically by the open-source control tower. All figures are reproducible with `SEED = 42`; see `README.md` for the mathematical specification.
