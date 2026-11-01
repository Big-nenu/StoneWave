"""API HTTP local de StoneWave (solo biblioteca estandar, sin dependencias extra).

Uso:   python api.py [--host 127.0.0.1] [--port 8765]

Endpoints (todos responden JSON; los POST exigen Content-Type: application/json):
  POST /inject  {"content": "...", "energy": 0.5, "namespace": "default", "fijo": false}
  POST /query   {"text": "...", "namespace": "default"}
  POST /dream   {"namespace": "default"}
  GET  /health

Seguridad: por defecto solo escucha en 127.0.0.1, rechaza peticiones cuyo Host no sea local
(protege contra paginas web maliciosas) y, si defines STONEWAVE_API_TOKEN, exige
"Authorization: Bearer <token>".
"""
import argparse
import hmac
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mem_engine import StoneWaveEngine, env_var

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
MAX_BODY = 1_000_000  # 1 MB


def make_server(engine, host="127.0.0.1", port=8765, token=None, default_ns="default"):
    lock = threading.Lock()  # el motor no esta pensado para llamadas simultaneas dentro del mismo proceso
    check_host = host in LOOPBACK

    class Handler(BaseHTTPRequestHandler):
        server_version = "StoneWaveAPI/1.0"

        def log_message(self, fmt, *args):
            print("StoneWave API:", fmt % args, file=sys.stderr)

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _guard(self):
            """True si la peticion puede seguir; si no, ya respondio el error."""
            if check_host:
                hdr = self.headers.get("Host", "")
                name = hdr.rsplit(":", 1)[0] if (":" in hdr and not hdr.endswith("]")) else hdr
                if name.lower() not in LOCAL_HOSTS:
                    self._send(403, {"error": "Host no permitido"})
                    return False
            if token:
                sent = self.headers.get("Authorization", "")
                if not hmac.compare_digest(sent, f"Bearer {token}"):
                    self._send(401, {"error": "Falta o es incorrecto el token (Authorization: Bearer ...)"})
                    return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            if self.path in ("/", "/health"):
                with lock:
                    n = len(engine.get_all_facts(default_ns))
                self._send(200, {"status": "ok", "app": "StoneWave", "namespace": default_ns, "memories": n,
                                 "endpoints": ["POST /inject", "POST /query", "POST /dream", "GET /health"]})
            else:
                self._send(404, {"error": "No existe esa ruta"})

        def _read_json(self):
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._send(415, {"error": "Usa Content-Type: application/json"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "Content-Length invalido"})
                return None
            if length > MAX_BODY:
                self._send(413, {"error": "Cuerpo demasiado grande (maximo 1 MB)"})
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "JSON invalido"})
                return None
            if not isinstance(data, dict):
                self._send(400, {"error": "El cuerpo debe ser un objeto JSON"})
                return None
            return data

        def do_POST(self):
            if not self._guard():
                return
            if self.path not in ("/inject", "/query", "/dream"):
                self._send(404, {"error": "No existe esa ruta"})
                return
            data = self._read_json()
            if data is None:
                return
            ns = data.get("namespace", default_ns)
            if not isinstance(ns, str) or not ns or len(ns) > 100:
                self._send(400, {"error": "namespace debe ser un texto de 1 a 100 caracteres"})
                return
            try:
                if self.path == "/inject":
                    content = data.get("content")
                    if not isinstance(content, str) or not content.strip():
                        self._send(400, {"error": "Falta 'content' (texto del recuerdo)"})
                        return
                    try:
                        energy = min(1.0, max(0.0, float(data.get("energy", 0.5))))
                    except (TypeError, ValueError):
                        self._send(400, {"error": "'energy' debe ser un numero entre 0 y 1"})
                        return
                    with lock:
                        result = engine.inject(content, energy=energy, namespace=ns, pinned=bool(data.get("fijo", False)))
                    ok = not str(result).startswith("❌")
                    self._send(200 if ok else 503, {"ok": ok, "result": str(result)})
                elif self.path == "/query":
                    text = data.get("text")
                    if not isinstance(text, str) or not text.strip():
                        self._send(400, {"error": "Falta 'text' (la pregunta)"})
                        return
                    with lock:
                        res = engine.query(text, namespace=ns)
                    if isinstance(res, str):
                        if res.startswith("Error"):
                            self._send(503, {"ok": False, "error": res})
                        else:
                            self._send(200, {"ok": True, "results": [], "message": res})
                    else:
                        self._send(200, {"ok": True, "results": [
                            {"sim": round(float(sim), 4), "id": fid, "energy": round(float(energy), 3), "content": content}
                            for sim, fid, energy, content in res]})
                else:  # /dream
                    with lock:
                        result = engine.dream(namespace=ns)
                    self._send(200, {"ok": True, "result": str(result)})
            except Exception as ex:  # no se filtra el detalle interno al cliente
                print(f"StoneWave API: error interno: {ex!r}", file=sys.stderr)
                self._send(500, {"error": "Error interno del motor"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description="StoneWave - API HTTP local")
    parser.add_argument("--host", default=env_var("API_HOST", "127.0.0.1"), help="Direccion donde escuchar (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(env_var("API_PORT", "8765")), help="Puerto (default: 8765)")
    args = parser.parse_args()
    token = env_var("API_TOKEN")
    if args.host not in LOOPBACK and not token:
        print("StoneWave API: AVISO: escuchas fuera de localhost sin STONEWAVE_API_TOKEN; cualquiera en tu red podria leer y escribir tu memoria.", file=sys.stderr)
    engine = StoneWaveEngine(db_path=env_var("DB"))
    server = make_server(engine, args.host, args.port, token, env_var("NAMESPACE", "default"))
    print(f"StoneWave API escuchando en http://{args.host}:{args.port} (Ctrl+C para salir)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    main()
