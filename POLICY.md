# Decision Policy: Lumen Cloud Reply Approval Gate

**Persona:** Customer support lead at Lumen Cloud (fictional SaaS).
**Gated action:** `send_email` to a customer. A wrong price or refund promise in a sent email can't be taken back, and the company may have to honor it.

## How a decision is made

```
draft + customer context ──► 1. Our own data check (price_sheet.csv, policies.json)   ─┐
                         └─► 2. Ezio /v1/analyze (tier, accuracy, claims, tone/bias/agreement) ─┴─► strictest wins ─► APPROVE → send_email
                                                                                                                    HOLD / BLOCK → not sent
```

Each check can raise the decision but none can lower it: APPROVE < HOLD_FOR_REVIEW < BLOCK. The reason names every check that fired. The evidence lists every fact checked: `[OK]` verified, `[BLOCK]` wrong, `[HOLD]` needs a human.

## Signals and thresholds

| Signal | Rule | Decision | Why |
|---|---|---|---|
| **Price** (`$49`, "forty-nine dollars", `/mo`, yearly totals) | Must equal the plan's monthly, annual-per-month, or loyalty-discounted price; Enterprise is custom | BLOCK if wrong | Rule 1: a wrong price is a promise we would have to honor. |
| **Discounts** | Loyalty 15%, annual 20%; no stacking; the plan must be eligible; the customer's plan and tenure (from context) must qualify | BLOCK if wrong; HOLD if an unknown % is offered or eligibility is unknown | Rule 2: an ineligible discount is still a wrong commitment. An off-policy discount isn't provably false, so a manager decides. |
| **Refunds** | Monthly 30 days, annual 14 days; "any time" or "full year" is wrong | BLOCK | Rule 1. |
| **Free trial** | 14 days, Pro and Business only, no card | BLOCK | Rule 1. |
| **First response** | Starter 48h, Pro 24h, Business 4h, matched to the plan named in the sentence | BLOCK | Rule 1; multi-fact sentences are checked fact by fact. |
| **Promises** (ship a feature, dates, waivers, exceptions) | Pattern match | HOLD | Rule 3: not provably false, but outside agent authority. |
| **Hype** ("guaranteed", `!!!`, ALL CAPS) | Pattern match | HOLD | Rule 4: backup for when Ezio's tone score is borderline. |
| **Ezio contradicted claim** | Any `claims[].verdict == contradicted` | BLOCK | Rule 6: Ezio covers public facts (e.g. GDPR) our files can't. A *supported* claim is not held. |
| **Ezio tier** | GREEN → no objection, YELLOW → HOLD, RED → BLOCK | as listed | GREEN only means "nothing found", so it never overrides our own checks. |
| **Tone / bias / agreement** | Below **60** | HOLD | Normal replies score 90+; hype scores ~26. 60 leaves margin on both sides. Low agreement means the draft echoes the customer. |
| **Accuracy `not_scored`** | Ignored | n/a | Rule 5: Ezio can't see our prices, so most emails come back not_scored. Our data check covers those facts. |
| **Accuracy `unavailable`** | HOLD (BLOCK if our data finds an error) | HOLD | Public claims went unchecked and our files can't cover them. |

## Failure behavior (fail closed)

| Situation | Behavior |
|---|---|
| 429 / 503 | Retries with backoff (up to 4 attempts, 45 s budget, honors `Retry-After`). |
| Ezio down, slow beyond budget, or malformed | Our data check still runs. If it proves a fact wrong: **BLOCK**. Otherwise: **HOLD_FOR_REVIEW**, so a person decides. We never APPROVE without Ezio's tone and fact-check. |
| Accuracy unavailable | HOLD, or BLOCK on a data error (above). |

The good probe is held during an outage on purpose. A short delay on a correct email costs less than sending a hype-laden or legally wrong one unchecked.

## Human review and audit

- HOLD drafts get a **manager review** step on the persona page: a name and reason are required, the choice is send or reject, and both are written to `audit_log.jsonl`. **BLOCK cannot be overridden.**
- Every decision logs the input, context, full Ezio response, evidence, decision, reason, and timestamp. `actions.log` gets a line only when an email is actually sent.

## Known limits

- Rule-based extraction: unusual phrasing (e.g. "a fifth off") can slip past the figure checks; Ezio's signals remain the backstop.
- "Agreeing with a customer's incorrect claim" relies on Ezio's agreement score, since we don't see the customer's message.
