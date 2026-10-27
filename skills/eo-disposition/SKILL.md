---
name: eo-disposition
description: Reduce excess and obsolete (E&O) inventory exposure on a component by choosing, in order, PO cancellation, PO push-out, or a disposition review, without starving any platform or service demand. Use for exception types EXCESS and OBSOLESCENCE_RISK.
allowed-tools: compute_eo_exposure get_open_pos get_where_used get_part_position get_decision_history simulate_scenario
metadata:
  owner: supply-planning
  version: "1.0"
---

# E&O disposition

Excess is inventory beyond what the next 52 weeks need plus safety stock. Obsolete risk is inventory beyond what the part's platforms will ever need, through end of service. The cheapest dollar of E&O is the one you never receive, so act on open POs first.

## Steps

1. **Size the exposure.** `compute_eo_exposure`: supply, 52-week need, safety stock, excess units and dollars, cancellable quantity, avoidable dollars, residual, and obsolete units and dollars.
2. **Check every consumer.** `get_where_used`. Netting is already at part level, but confirm that no platform is ramping and that service demand to EOSL is covered before you cut supply.
3. **Look for near-term risk on the same part.** `get_part_position`. If the part also has a near-term shortage, the problem is timing, not quantity: pulling in a late PO may fix both, but you cannot recommend a pull-in here. Report the near-term shortage in your rationale and let the reconcile step consolidate.
4. **Work the POs.** `get_open_pos`: which POs are cancellable (outside the cancellation window) and which can only be pushed out.
5. **Test the change.** `simulate_scenario` with the proposed cancel or push-out to show no shortage is created inside the horizon. Cite the result.
6. **Check history.** `get_decision_history`: was a cancellation already requested or rejected?

## Disposition hierarchy

| Order | Action | When | Quantity source |
|---|---|---|---|
| 1 | CANCEL_PO | Cancellable qty > 0 | `cancel_qty_recommended` from `compute_eo_exposure` |
| 2 | PUSH_OUT_PO | Excess remains and POs are inside the cancel window | residual units; name the POs |
| 3 | DISPOSITION_REVIEW | Residual on-hand excess, or obsolete units | residual or obsolete units; options: return to vendor, broker resale, scrap |
| — | MONITOR | Excess with no open POs on an active platform that will consume it beyond 52 weeks | cite the lifetime need |

Never cancel more than the excess. Never cut supply that a simulation shows is needed inside the horizon.

## Root cause wording

- EXCESS_FORECAST_DROP: demand fell versus the previous forecast version.
- EXCESS_OVERBUY: open POs exceed need with no forecast change.
- LIFECYCLE_END: platform end of life left stranded supply.

## Pitfalls

- Cancelling a PO that a ramping or shared platform needs.
- Scrapping service spares a customer contract still requires.
- Reporting excess in units only: Finance needs dollars.
- Cancelling and expediting the same part in one week.

## Output

Fill the Recommendation schema with evidence references. Stakeholders: GSM for PO changes, Finance for exposure and reserve, PM if a platform end of life is the cause.
