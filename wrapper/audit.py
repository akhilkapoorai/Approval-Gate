"""Audit log: one JSON line per decision. Done for you; extend the fields if your policy needs more."""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# gate.py judge/try set GATE_LOG_DIR so evaluation runs never pollute your demo audit sample.
LOG = Path(os.getenv("GATE_LOG_DIR") or Path(__file__).parent) / "audit_log.jsonl"
_lock = threading.Lock()


def record(persona, text, context, decision):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "id": decision.id,
        "persona": persona,
        "input": text,
        "context": context,
        "decision": decision.decision,
        "reason": decision.reason,
        "evidence": decision.evidence,
        "ezio": decision.ezio,
    }
    with _lock, LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def record_review(persona, decision_id, text, verdict, reviewer, note, action_executed):
    """A human's call on a HOLD_FOR_REVIEW decision, with their reason."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "id": decision_id,
        "persona": persona,
        "input": text,
        "decision": "REVIEW_" + verdict.upper(),
        "reason": f"{reviewer}: {note}",
        "reviewer": reviewer,
        "action_executed": action_executed,
    }
    with _lock, LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def recent(limit=25):
    if not LOG.exists():
        return []
    lines = LOG.read_text().splitlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ezio = e.get("ezio") or {}
        verdict = ezio.get("verdict") or {}
        out.append({k: e.get(k) for k in ("timestamp", "id", "persona", "input", "decision", "reason", "reviewer")}
                   | {"ezio": f"{verdict.get('tier')} {verdict.get('score')}" if verdict else (ezio.get("error") and "error: " + ezio["error"])})
    return out
