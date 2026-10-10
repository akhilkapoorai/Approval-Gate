"""Your wrapper. Start with check_source_of_truth() and policy.json.

Flow: call Ezio -> read its signals -> check your persona's source of truth -> apply policy
-> decision (+ reason, evidence) -> audit log -> run the mock action only if approved.

Run one input:  python3 wrapper.py customer_support "Your 30% loyalty discount applies to the Pro plan."
Score yourself: python3 evaluate.py customer_support
"""
import csv
import json
import math
import sys
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import audit
import mock_actions
import support_rules
from ezio_client import EzioError, analyze

HERE = Path(__file__).resolve().parent
PERSONAS = HERE.parent / "personas"
POLICY = json.loads((HERE / "policy.json").read_text())

APPROVE, HOLD, BLOCK = "APPROVE", "HOLD_FOR_REVIEW", "BLOCK"
PASS = "PASS"
RANK = {APPROVE: 0, HOLD: 1, BLOCK: 2}


@dataclass
class Signals:
    tier: str
    score: float
    summary: str
    accuracy_status: str
    claims_total: int
    claims_verified: int
    claims_contradicted: int
    tone: float
    bias: float
    agreement: float
    claims: list = field(default_factory=list)


@dataclass
class Finding:
    level: str
    message: str


@dataclass
class Decision:
    decision: str
    reason: str
    evidence: list
    ezio: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


def parse_signals(resp):
    try:
        verdict, dims = resp["verdict"], resp["dimensions"]
        acc = dims["accuracy"]
        if verdict["tier"] not in ("GREEN", "YELLOW", "RED") or acc["status"] not in ("scored", "not_scored", "unavailable"):
            raise ValueError("invalid tier or accuracy status")
        scores = [verdict["score"]] + [dims[d]["score"] for d in ("tone", "bias", "agreement")]
        if acc["status"] == "scored":
            scores.append(acc["score"])
        if any(isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or not 0 <= s <= 100 for s in scores):
            raise ValueError("scores must be finite numbers between 0 and 100")
        if not isinstance(verdict["summary"], str) or not isinstance(resp.get("claims", []), list):
            raise ValueError("invalid summary or claims")
        return Signals(
            tier=verdict["tier"], score=verdict["score"], summary=verdict["summary"],
            accuracy_status=acc["status"], claims_total=acc.get("claims_total", 0),
            claims_verified=acc.get("claims_verified", 0), claims_contradicted=acc.get("claims_contradicted", 0),
            tone=dims["tone"]["score"], bias=dims["bias"]["score"], agreement=dims["agreement"]["score"],
            claims=resp.get("claims", []),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise EzioError("malformed mock response", resp) from e


@lru_cache(maxsize=None)
def load_source_of_truth(persona):
    """Every .json and .csv file in the persona folder, keyed by file name (for example sot['policies'])."""
    data = {}
    for f in sorted((PERSONAS / persona).iterdir()):
        if f.stem in ("practice_set", "business_rules"):
            continue
        if f.suffix == ".json":
            data[f.stem] = json.loads(f.read_text())
        elif f.suffix == ".csv":
            with f.open() as handle:
                data[f.stem] = list(csv.DictReader(handle))
    return data


def check_source_of_truth(text, context, sot, persona="customer_support"):
    """Compare the draft with the persona's own data. Returns Finding(level, message) where level is
    BLOCK, HOLD_FOR_REVIEW, or PASS (a fact we verified; evidence only, never changes the decision)."""
    if persona == "customer_support":
        return [Finding(level, msg) for level, msg in support_rules.check(text, context, sot)]
    return []


def ezio_claim_findings(signals):
    """Ezio's per-claim fact-check: a contradicted claim blocks even if the overall tier looks fine."""
    out = []
    for c in signals.claims:
        if not isinstance(c, dict):
            continue
        claim = c.get("text") or c.get("claim") or "a claim"
        src = c.get("source")
        if isinstance(src, dict):
            src = src.get("publisher") or src.get("url")
        source = f" (source: {src})" if src else ""
        if c.get("verdict") == "contradicted":
            out.append(Finding(BLOCK, f"Ezio fact-check contradicts: \"{claim}\"{source}"))
        elif c.get("verdict") == "supported":
            out.append(Finding(PASS, f"Ezio fact-check supports: \"{claim}\"{source}"))
    if signals.claims_contradicted and not any(f.level == BLOCK for f in out):
        out.append(Finding(BLOCK, f"Ezio reports {signals.claims_contradicted} contradicted claim(s)."))
    return out


def apply_policy(signals, findings, policy):
    decision, reasons = APPROVE, []

    def raise_to(level, reason):
        nonlocal decision
        reasons.append(reason)
        if RANK[level] > RANK[decision]:
            decision = level

    for f in findings:
        if f.level != PASS:
            raise_to(f.level, f.message)
    for dim, rule in policy["dimension_minimums"].items():
        score = getattr(signals, dim)
        if score < rule["hold_below"]:
            raise_to(HOLD, f"Ezio {dim} score is {score}, below our minimum of {rule['hold_below']}")
    if signals.accuracy_status == "not_scored" and policy["when_accuracy_not_scored"] != "ignore":
        raise_to(policy["when_accuracy_not_scored"], "accuracy was not scored (no claims checked)")
    if signals.accuracy_status == "unavailable":
        raise_to(policy["when_accuracy_unavailable"], "fact-check was unavailable")
    tier_decision = policy["tier_decisions"][signals.tier]
    if tier_decision != APPROVE:
        raise_to(tier_decision, f"Ezio tier is {signals.tier} ({signals.score})")
    if not reasons:
        reasons.append(f"No issues found (Ezio {signals.tier} {signals.score})")
    return reasons, decision


def evidence_lines(findings):
    tag = {BLOCK: "[BLOCK]", HOLD: "[HOLD]", PASS: "[OK]"}
    return [f"{tag[f.level]} {f.message}" for f in findings]


def decide(text, context=None, persona="customer_support", scenario=None, audit_log=True):
    context = context or {}
    # Our own data check never depends on Ezio, so it still works when Ezio is down.
    findings = check_source_of_truth(text, context, load_source_of_truth(persona), persona)
    try:
        response = analyze(text, scenario=scenario)
        signals = parse_signals(response)
    except EzioError as e:
        # Fail closed: without Ezio we can't see tone, bias, or public-law claims. A fact our own data
        # proves wrong is still a BLOCK; anything else goes to a human.
        blocks = [f for f in findings if f.level == BLOCK]
        decision = BLOCK if blocks else POLICY["on_ezio_failure"]
        reason = "; ".join(f.message for f in blocks) if blocks else "Ezio could not score this draft, so a person must review it before sending"
        result = Decision(decision, f"Ezio unavailable ({e}). {reason}",
                          evidence_lines(findings) + [f"[HOLD] Ezio error: {e}"],
                          {"error": str(e), "response": e.response})
    else:
        findings += ezio_claim_findings(signals)
        reasons, decision = apply_policy(signals, findings, POLICY)
        if decision == APPROVE and any(f.level == PASS for f in findings):
            reasons = [f"All checkable facts match company data (Ezio {signals.tier} {signals.score})"]
        result = Decision(decision, "; ".join(reasons), evidence_lines(findings) + [
            f"Ezio: {signals.tier} {signals.score}, accuracy {signals.accuracy_status}, "
            f"tone {signals.tone}, bias {signals.bias}, agreement {signals.agreement}"], response)
    if audit_log:
        audit.record(persona, text, context, result)
    return result


def gate(text, context=None, persona="customer_support"):
    result = decide(text, context, persona)
    print(f"{result.decision}: {result.reason}")
    if result.decision == APPROVE:
        mock_actions.run_for_persona(persona, text)
    else:
        print("[GATE] action not executed")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('usage: python3 wrapper.py <persona> "<text>" [context-json]')
    gate(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}, sys.argv[1])
