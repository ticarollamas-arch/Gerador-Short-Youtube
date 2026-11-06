"""Minimal HTTP wrapper so the pipeline runs as a Railway web service.

Listens on $PORT (injected by Railway). Routes:
  GET  /            simple form
  GET  /health      liveness probe
  POST /api/shorts  {"url": "...", "num_clips": 3, "mode": "api"} -> {"job_id": ...}
  GET  /api/shorts/<job_id>  job status / result
"""
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shorts_generator import generate_shorts

JOBS: dict = {}

PAGE = """<!doctype html><meta charset=utf-8><title>Gerador de Shorts</title>
<body style="font-family:sans-serif;max-width:640px;margin:3rem auto">
<h1>Gerador de Shorts do YouTube</h1>
<form id=f><input id=u placeholder="URL do YouTube" style="width:100%;padding:8px" required>
<p><button>Gerar</button></p></form><pre id=o></pre>
<script>
f.onsubmit=async e=>{e.preventDefault();o.textContent='Enviando...';
const r=await fetch('/api/shorts',{method:'POST',body:JSON.stringify({url:u.value})});
const {job_id}=await r.json();
const t=setInterval(async()=>{const j=await(await fetch('/api/shorts/'+job_id)).json();
o.textContent=JSON.stringify(j,null,2);if(j.status!='running')clearInterval(t)},3000)}
</script>"""


def _run(job_id, body):
    try:
        res = generate_shorts(
            youtube_url=body["url"],
            num_clips=int(body.get("num_clips", 3)),
            aspect_ratio=body.get("aspect_ratio", "9:16"),
            mode=body.get("mode", "api"),
        )
        JOBS[job_id] = {"status": "done", "shorts": res["shorts"], "highlights": res["highlights"]}
    except Exception as e:
        JOBS[job_id] = {"status": "failed", "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else (
            payload.encode() if isinstance(payload, str) else json.dumps(payload).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self.path.startswith("/api/shorts/"):
            job = JOBS.get(self.path.rsplit("/", 1)[1])
            return self._send(200 if job else 404, job or {"error": "not found"})
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/shorts":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            assert body.get("url")
        except Exception:
            return self._send(400, {"error": "JSON body with 'url' required"})
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "running"}
        threading.Thread(target=_run, args=(job_id, body), daemon=True).start()
        self._send(202, {"job_id": job_id})


if __name__ == "__main__":
    port = int(os.environ["PORT"]) if os.environ.get("PORT") else 8080
    print(f"listening on 0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
