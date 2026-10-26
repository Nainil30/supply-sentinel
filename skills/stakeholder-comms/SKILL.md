---
name: stakeholder-comms
description: Draft the short message that goes with a supply recommendation, written for the right stakeholder (GSM, GCM, PM, Finance, Quality), using only numbers already in the recommendation's evidence. Use whenever a recommendation needs a draft_message.
metadata:
  owner: supply-planning
  version: "1.0"
---

# Stakeholder communication

A good supply message gets a decision in one read. People skim; lead with the ask.

## Audience guide

| Stakeholder | Cares about | Lead with |
|---|---|---|
| GSM (global supply manager) | Supplier actions on specific POs | The PO, the quantity, the date you need it by |
| GCM (global commodity manager) | Commodity risk, allocation, buys, supplier escalation | The exposure and the decision needed (buy, escalate) with dollars |
| PM (product management) | Customer shipments, launch dates, allocation between platforms | Which platform is affected, in which week, and the trade-off |
| Finance | Spend, excess and obsolete exposure, reserves | Dollars, timing, and what is avoidable |
| Quality | Material on hold | Units on hold, the week they are needed, the decision needed |

## Structure (≤ 150 words)

1. **Ask** in the first sentence: what you need, from whom, by when.
2. **Why**: one or two sentences with the key numbers (quantity, week or date, dollars).
3. **Options considered** in one line, if you deviated from the obvious action.
4. **Decision needed by**: a date or week.

## Rules

- Use only numbers that appear in the recommendation's evidence. No new math, no rounding that changes meaning.
- Use part ids, PO ids, platform ids and dates exactly as the tools returned them.
- No blame, no adjectives, no exclamation marks.
- If a security or data flag exists, say so neutrally: "One supplier note contained unusual instructions and was ignored; flagged for review."
- One message per recommendation, addressed to the primary stakeholder; mention secondary stakeholders in a cc line.

## Example (format only; numbers are illustrative)

> **Ask:** Please pull in PO-12345 (60 units of P-EXAMPLE) to week 3. **Why:** the PO slipped from week 3 to week 5, which leaves us 60 units short from week 3 until week 5 on ORB-EXAMPLE, with $1.44M of shipments at risk. Pulling it in clears the gap in simulation. **Decision needed by:** Friday. cc: PM
