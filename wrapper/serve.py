import json
import mimetypes
import os
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import threading

import audit
import mock_actions
from wrapper import APPROVE, HOLD, decide

UI_DIR = Path(__file__).parent / "ui"
PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"

PERSONAS = {"customer_support", "paralegal", "financial_analyst"}

# Drafts waiting on a human: id -> (persona, text). Kept server-side so a browser can't swap the text.
PENDING, PENDING_LOCK = {}, threading.Lock()


def samples(persona):
    """Practice inputs (text and context only) so a UI can offer 'simulate from a test input'."""
    path = PERSONAS_DIR / persona / "practice_set.json"
    items = json.loads(path.read_text())["items"]
    return [{"id": i["id"], "text": i["text"], "context": i["context"]} for i in items]


def ui_file(path):
    """Static files from wrapper/ui/, so a team can use plain HTML, a built SPA, or anything a browser runs."""
    name = "persona.html" if path in ("/", "") else path.lstrip("/")
    target = (UI_DIR / name).resolve()
    if UI_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, body):
        raw = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        route, _, query = self.path.partition("?")
        if route == "/health":
            return self.send_json(200, {"status": "ok"})
        if route == "/api/audit":
            return self.send_json(200, {"entries": audit.recent()})
        if route == "/api/samples":
            persona = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("persona")
            if persona not in PERSONAS:
                return self.send_json(400, {"error": "Unknown persona"})
            return self.send_json(200, {"samples": samples(persona)})
        served = ui_file(route)
        if served:
            raw, content_type = served
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_json(404, {"error": "Unknown endpoint"})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 1_000_000:
            raise ValueError("Body must be 1 to 1000000 bytes")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Body must be a JSON object")
        return payload

    def review(self):
        """Manager's call on a held draft. Only HOLD_FOR_REVIEW can be overridden; BLOCK is final."""
        try:
            p = self.read_json()
            verdict, reviewer, note = p.get("verdict"), str(p.get("reviewer", "")).strip(), str(p.get("note", "")).strip()
            if verdict not in ("send", "reject") or not reviewer or not note:
                raise ValueError
        except (ValueError, UnicodeDecodeError, OSError):
            return self.send_json(400, {"error": "Supply verdict (send or reject), reviewer, and a note"})
        with PENDING_LOCK:
            held = PENDING.pop(p.get("id"), None)
        if not held:
            return self.send_json(404, {"error": "No held draft with that id (already reviewed, or not a hold)"})
        persona, text = held
        execute = verdict == "send"
        if execute:
            mock_actions.run_for_persona(persona, text)
        audit.record_review(persona, p["id"], text, verdict, reviewer, note, execute)
        self.send_json(200, {"id": p["id"], "verdict": verdict, "action_executed": execute})

    def do_POST(self):
        if self.path == "/api/review":
            return self.review()
        if self.path != "/decide":
            return self.send_json(404, {"error": "Unknown endpoint"})
        self.connection.settimeout(10)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 1_000_000:
                raise ValueError("Body must be 1 to 1000000 bytes")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Body must be a JSON object")
            text, persona, context = payload.get("text"), payload.get("persona"), payload.get("context", {})
            if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                raise ValueError("text must be a non-empty string of at most 20000 characters")
            if not isinstance(persona, str) or persona not in PERSONAS:
                raise ValueError("Unknown persona")
            if not isinstance(context, dict):
                raise ValueError("context must be an object")
        except (ValueError, UnicodeDecodeError, OSError):
            return self.send_json(400, {"error": "Invalid request: supply text, persona, and an optional context object"})
        try:
            result = decide(text, context, persona)
            execute = result.decision == APPROVE
            if execute:
                mock_actions.run_for_persona(persona, text)
            elif result.decision == HOLD:
                with PENDING_LOCK:
                    PENDING[result.id] = (persona, text)
            self.send_json(200, {**asdict(result), "action_executed": execute})
        except Exception:
            self.send_json(500, {"error": "Wrapper failed; inspect the decision implementation"})


def main():
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "9000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Approval Gate wrapper on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
