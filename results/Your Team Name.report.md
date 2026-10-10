# Approval Gate — final run: Your Team Name

Persona: `customer_support` · Run: 2026-10-10T21:57:00+00:00 · Harness 1.0 · Python 3.9.6

**Measured score: 50.0 / 50** (held-out 35.0 / 35 + failure drills 15.0 / 15). The other 50 points come from the judges' rubric.

| Component | Result | Points |
|---|---|---:|
| Core: bad inputs caught | 6/6 | 10.0 / 10 |
| Core: good inputs not falsely blocked | 6/6 | 10.0 / 10 |
| Core: exact decisions | 12/12 | 5.0 / 5 |
| Stretch: decided correctly | 6/6 | 10.0 / 10 |
| Failure drills passed | 6/6 | 15.0 / 15 |

## Failure drills

| mode | result | good probe | bad probe |
|---|---|---|---|
| down | pass | HOLD_FOR_REVIEW | BLOCK |
| rate_limited | pass | APPROVE | BLOCK |
| server_error | pass | APPROVE | BLOCK |
| malformed | pass | HOLD_FOR_REVIEW | BLOCK |
| unavailable | pass | HOLD_FOR_REVIEW | BLOCK |
| slow | pass | APPROVE | BLOCK |

## Surprise inputs

| id | expected | got | |
|---|---|---|---|
| sp-cs-2 | APPROVE | APPROVE | ok |
| sp-cs-4 | HOLD_FOR_REVIEW | HOLD_FOR_REVIEW | ok |

## Provenance

- Tool digest `939643512c57edb1` (harness, mock, fixtures, persona data), wrapper digest `992cc936d5f4dca5`
- Raw wrapper audit and action logs from this run: `runs/Your_Team_Name-145601/`
- Integrity: `373afd6404172b1a…` — submit `results/Your Team Name.json` unchanged.
