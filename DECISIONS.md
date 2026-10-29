# Design decisions

The README is the operating document: it specifies the mathematics and the
constraints. This file records the choices behind them - what was rejected and
what each choice costs. Every claim here is tied to a file or a policy constant
so it can be checked rather than believed.

| # | Decision | Rejected alternative | Main cost |
|---|---|---|---|
| 1 | Engine is the source of truth; Excel presents its decisions | Re-derive policy in worksheet formulas | Workbook cannot run without the Python pipeline |
| 2 | Six tabs, one per decision stage | Single flat sheet or a BI dashboard | 8,048 formulas to build and keep parseable |
| 3 | Weekly cadence, one 20′ GP | Daily ordering, LCL, 40′ HC every week | 28.8 CBM forces explicit prioritisation |
| 4 | 32-day FBA shield as a hard constraint | Expected-cost optimisation of the fee | Extra FBA inventory and aged risk |
| 5 | Supplier MOQ beats the 135-day DOS cap | DOS cap wins, SKU skipped | Occasional flagged overstock |
| 6 | 5,000-unit aggregate PO floor | Order only triggered SKUs | Some cartons pulled forward early |
| 7 | Widening-window ladder for censored demand | κ = 7 credibility blend | Noisier estimate for sparse SKUs |
| 8 | No ML in this repository | Learned demand model in the engine | No learned seasonality here |
| 9 | Walk-forward backtest on nine cutoffs | Random train/test split | Recovery test, not out-of-sample |
| 10 | Removal only when it pays back within 12 months | Clear all aged stock | Some aged units stay and keep accruing |
| 11 | Synthetic catalogue, declared as such | Real or client transaction data | No market realism claim |

---

## 1. The engine computes the policy; Excel does not re-derive it

**Decision.** `src/engine/` produces unconstrained demand, safety stock, ROP and
S_max, the transfer, the factory PO and the container plan. Those values are
written to the workbook as data. The workbook keeps the *arithmetic* live  - 
units, CBM, fee exposure, MOQ/DOS checks and every KPI aggregate - but it does
not invent a policy of its own.

**Why.** Two implementations of the same rule drift. The tests can pin the
Python engine exactly; they can only parse the worksheet formulas. If the
workbook re-derived S_max, a policy change could pass CI and still disagree
with the workbook on the operator's screen.

**Rejected.** A workbook that owns the policy, with Python only exporting
inputs. It would be more "Excel-native", but it would make the tested artefact
and the signed-off artefact different objects.

**Cost.** The workbook is not self-contained: it needs the pipeline run first.
A reader who opens the committed file without recalculating sees blanks,
because formula cells are saved without cached values.

**Check.** `src/deliverables/excel_generator.py`; §7 of the README;
`tests/test_excel_workbook.py`.

---

## 2. Six tabs, one per decision stage

**Decision.** `00_Exec_Control_Tower`, `01_Catalog_&_Parameters`,
`02_Demand_Unconstraining`, `03_FBA_Nivel1_Transfer`,
`04_Factory_Nivel2_PO_&_CBM`, `05_Fee_Risk_Audit`.

**Why.** Each tab answers one question an operator signs off: what is true
demand, what moves to FBA, what the factory ships, what must be removed. The
cockpit aggregates them. A reviewer can audit a number by walking one tab to
its left-hand neighbour.

**Rejected.** A single flat sheet (unreadable at 160 SKUs) and a BI dashboard
(interactive, but not a sign-off artefact an operator can annotate and file).

**Cost.** Cross-tab references and structured tables make the workbook
expensive to build; 8,048 formula cells, which Excel reports as 8,070 because
four merged ranges inherit their anchor's formula.

**Check.** README §7; `outputs/Weekly_Operations_Replenishment_Model.xlsx`.

---

## 3. Weekly cadence on a single 20′ GP

**Decision.** One weekly ocean cycle, a 20′ GP with 28.8 CBM usable, LCL below
15 CBM and a 40′ HC above 28.8 CBM.

**Why.** The factory lane is 75 days door to door with a 10-day standard
deviation (`L2_MEAN_DAYS`, `L2_STD_DAYS`). A weekly decision is fast enough to
react to a fee cliff and slow enough to consolidate a container. At this
volume the 20′ GP is the smallest FCL that clears the aggregate PO floor
without carrying more than the DOS cap allows.

**Rejected.** Daily ordering (no consolidation, no container fill) and shipping
LCL every week (unit cost too high for this volume). A 40′ HC is selected only
when the plan genuinely exceeds 28.8 CBM.

**Cost.** The CBM ceiling binds before the DOS cap in a heavy week, so the
knapsack must drop or defer cartons by profit velocity rather than by need
alone. The 1.8 CBM top-up reserve (`TOPUP_RESERVE_CBM`) exists for exactly that
reason and is itself a policy choice.

**Check.** README §5.3; `CONTAINER_20GP_CBM`, `LCL_FCL_BREAKEVEN_CBM`.

---

## 4. The fee shield is a constraint, not a cost trade-off

**Decision.** Keep FBA above 32 days of supply (`FBA_SHIELD_DAYS`) - the 28-day
low-inventory threshold plus a 4-day cushion - and cap post-transfer FBA cover
at 120 days (`FBA_AGE_CAP_DAYS`).

**Why.** The low-inventory fee is assessed per FNSKU when *both* the 30-day and
90-day historical days of supply are below 28 days. A planner cannot see the
90-day series in the weekly feed without reconstructing it, so the shield
buys margin against the measurement lag. It is a hard constraint because a
breach is a cash penalty, not a variance to trade off.

**Rejected.** Minimising expected fee + holding cost jointly. That would let
the model "choose" a fee in a week where the fee is smaller than the carrying
cost, which is defensible in theory but hard to explain to an operator and
easy to game by under-shipping.

**Cost.** The shield over-ships slow SKUs relative to a pure cost optimum and
adds aged-inventory risk. The 120-day FBA age cap is the counterweight.

**Check.** `src/config.py` (`LOW_INVENTORY_FEE_*`, `FBA_SHIELD_DAYS`,
`FBA_AGE_CAP_DAYS`); README §5.2.

---

## 5. Supplier MOQ beats the DOS cap

**Decision.** If the 135-day cap allows fewer cartons than the supplier MOQ,
order the MOQ and flag the line `MOQ OVERRIDE` (`q_i^{MOQ}` branch in §5.3).

**Why.** An MOQ is a physical commercial constraint; the DOS cap is a planning
constraint. Pretending the cap wins would produce an order the supplier will
not accept. Surfacing the override keeps the violation visible instead of
silently truncating it.

**Rejected.** Skip the SKU until the cap can accommodate a full MOQ. That
creates a stockout to protect a planning rule.

**Cost.** A small number of lines carry more than 135 days of cover. They are
flagged, counted and visible in the factory tab, not hidden in an average.

**Check.** README §5.3; `DEFAULT_SKU_MOQ_UNITS`; `tests/test_engine.py`.

---

## 6. The 5,000-unit aggregate PO floor is met by pulling forward

**Decision.** Lift the weekly order to the 5,000-unit floor
(`DEFAULT_PO_AGGREGATE_MOQ_UNITS`) by advancing the nearest-to-breach SKUs in
exact SKU-MOQ increments.

**Why.** The floor is a factory commercial term. Advancing the closest
stockout keeps the extra units where they will be consumed soonest, rather
than spreading them across slow movers to make the arithmetic tidy.

**Rejected.** Top up the largest-velocity SKU regardless of days to breach.
That over-orders the fastest mover and under-protects the line about to run
out.

**Cost.** Some cartons arrive earlier than the pure need date, adding carrying
cost. The 135-day cap still applies after the pull-forward.

**Check.** README §5.3; `tests/test_engine.py` (aggregate floor).

---

## 7. Widen the window before you extrapolate

**Decision.** For each channel, use the 30-day active-day maximum-likelihood
rate; if the SKU had no active day, use 90 days; then 365 days; only then the
sister-family prior.

**Why.** A fully depleted SKU contributes zero to both numerator and
denominator of the active-day estimator, so it is undefined rather than zero.
Widening the window reuses the same estimator on the widest period in which the
SKU was genuinely on sale. The sister prior is last because its denominator
(active sibling share plus cannibalisation) is the most assumption-heavy term
in the model.

**Rejected.** The first shipped version, an empirical-Bayes credibility blend
with κ = 7 pseudo-days. It is still computed as a diagnostic column, but it is
not the shipped rule: on the nine backtest cutoffs it had +5.8% mean bias and
7.0% WAPE against +0.5% and 1.1% for the ladder, and its censored-subset WAPE
was 318.5% against 39.5%.

**Cost.** The 90- and 365-day windows mix regimes (pre-season, promotion,
post-cut) and are noisier for sparse SKUs. The sister prior remains available
and is exercised by tests.

**Check.** README §5.1 and §5.1.1; `src/engine/unconstraining.py`;
`src/diagnostics/estimator_backtest.py`.

---

## 8. No machine learning in this repository

**Decision.** The demand estimator is a deterministic censoring-aware ladder.
No learned model, no trained weights.

**Why.** The artefact an operator signs is a workbook with live formulas. A
learned model would move the number out of the auditable arithmetic and into a
pickle. This architecture focuses on deterministic operational replenishment, container constraints, and policy fee shields. Learned demand forecasting is addressed in a dedicated specialized model (`retail-demand-forecasting`); maintaining auditable deterministic rules here ensures complete formula traceability in the financial workbook.

**Rejected.** Adding a gradient-boosted demand model to the engine to look more
"ML". It would have no honest evaluation path inside a synthetic, weekly,
single-snapshot pipeline.

**Cost.** The engine cannot learn seasonality or promotions. It relies on
explicit windows, tier CV envelopes and the synthetic generator's structure.

**Check.** README §5.1; `src/engine/unconstraining.py`.

---

## 9. Walk forward, and call it a recovery test

**Decision.** Score the estimators on nine monthly cutoffs, each seeing only
the history available on that date, against the generator's latent FBA demand.

**Why.** A random split would leak future information into the estimator and
flatter it. The cutoffs mimic the order in which a planner would have met each
snapshot.

**Rejected.** A single holdout at the end of the series (too few observations)
and a random 80/20 split (leakage).

**Cost.** The target is the simulator's own latent draw, so it carries no
sampling error. This is a **recovery test against a known data-generating
process, not an out-of-sample forecast evaluation**; the README says so next to
the table, and the per-cutoff detail is exported so the claim can be checked.

**Check.** README §5.1.1; `data/processed/estimator_backtest.csv`;
`outputs/estimator_backtest.md`.

---

## 10. Remove only when the removal pays back

**Decision.** Recommend removal when the monthly surcharge the removal avoids would repay the $0.84/unit removal fee within 12 months (`AIS_PAYBACK_MONTHS`); otherwise recommend a 25% discount.

**Why.** Removal destroys the option value of selling the unit. The comparison
is explicit and auditable: fee avoided versus fee paid, with the tariff tiers
centralised in `AGED_INVENTORY_TIERS`.

**Rejected.** A blanket "clear everything over 270 days" rule. It is simpler,
but it pays removal fees on units that would sell at a discount and turns a
policy threshold into a loss.

**Cost.** Some aged units stay in the network and keep accruing surcharge. The
model surfaces them as `DISCOUNT 25% NOW` rather than silently accepting the
carry.

**Check.** README §5.4; `src/config.py` (`AGED_INVENTORY_TIERS`,
`REMOVAL_FEE_PER_UNIT_USD`, `AIS_PAYBACK_MONTHS`).

---

## 11. Synthetic catalogue, declared everywhere

**Decision.** Generate the catalogue, sales, inventory and purchase orders from
`seed=42`, 365 days, 160 child SKUs, and label the project synthetic in the
README, the profile and the workbook.

**Why.** The primary deliverable is the multi-echelon replenishment arithmetic, container knapsack optimization, and auditable financial sign-off workbook. Using a deterministic synthetic contract ensures full reproducibility without compromising proprietary client transaction data, allowing exact stress-test scenarios (3 critical, 4 penalty, 2 aged) to be audited deterministically.

**Rejected.** Scraping a public marketplace dataset and pretending it is a real
brand. It would add noise without adding decision realism, and it would
undermine the one thing this project can prove: the arithmetic.

**Cost.** No market realism claim. Fee schedules are 2026 policy inputs and
must be re-verified before production use.

**Check.** README §4 and §10; `src/config.py`; `data/processed/generation_truth.csv`.
