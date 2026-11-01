"""Pruebas de la API HTTP SIN Ollama (embeddings falsos).
Ejecutar:  python tests/test_api.py
"""
import os
import sys
import json
import threading
import http.client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import make_engine
from api import make_server


def start(token=None):
    e = make_engine()
    srv = make_server(e, "127.0.0.1", 0, token=token)  # puerto 0: el sistema elige uno libre
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def stop(srv):
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body) if body is not None else None)
    c.request(method, path, data, h)
    r = c.getresponse()
    payload = json.loads(r.read().decode())
    c.close()
    return r.status, payload


def test_inject_query_dream_health():
    srv, port = start()
    try:
        st, r = call(port, "POST", "/inject", {"content": "Luis estudió ingeniería en computación en la universidad Lamar"})
        assert st == 200 and r["ok"] and "NUEVA" in r["result"], (st, r)
        call(port, "POST", "/inject", {"content": "Los gatos duermen mucho durante el día"})
        st, r = call(port, "POST", "/query", {"text": "qué estudió Luis en la universidad"})
        assert st == 200 and r["results"][0]["content"].startswith("Luis estudió"), (st, r)
        assert set(r["results"][0]) == {"sim", "id", "energy", "content"}
        st, r = call(port, "POST", "/query", {"text": "astronomía de agujeros negros"})
        assert st == 200 and r["results"] == [] and r["message"], (st, r)
        st, r = call(port, "POST", "/dream", {})
        assert st == 200 and r["result"].startswith("Sleeptime"), (st, r)
        st, r = call(port, "GET", "/health")
        assert st == 200 and r["status"] == "ok" and r["memories"] == 2, (st, r)
    finally:
        stop(srv)


def test_namespaces_separados():
    srv, port = start()
    try:
        call(port, "POST", "/inject", {"content": "El telescopio de mi vecino tiene montura ecuatorial", "namespace": "astro"})
        st, r = call(port, "POST", "/query", {"text": "telescopio vecino montura ecuatorial", "namespace": "astro"})
        assert r["results"] and "telescopio" in r["results"][0]["content"], r
        st, r = call(port, "POST", "/query", {"text": "telescopio vecino montura ecuatorial"})  # namespace default
        assert r["results"] == [], r
    finally:
        stop(srv)


def test_fijo_y_energia():
    srv, port = start()
    try:
        call(port, "POST", "/inject", {"content": "Me llamo Luis Eduardo y vivo en Guadalajara", "fijo": True, "energy": 5})
        st, r = call(port, "POST", "/query", {"text": "me llamo luis eduardo vivo guadalajara"})
        assert r["results"][0]["energy"] <= 1.0, r  # la energia se limita a 1.0
    finally:
        stop(srv)


def test_errores_de_entrada():
    srv, port = start()
    try:
        assert call(port, "POST", "/inject", {"content": "   "})[0] == 400
        assert call(port, "POST", "/inject", {})[0] == 400
        assert call(port, "POST", "/query", {"text": 5})[0] == 400
        assert call(port, "POST", "/inject", {"content": "x", "energy": "mucha"})[0] == 400
        assert call(port, "POST", "/inject", {"content": "x", "namespace": ""})[0] == 400
        assert call(port, "POST", "/inject", raw="{no es json")[0] == 400
        assert call(port, "POST", "/inject", raw="[1, 2]")[0] == 400
        assert call(port, "POST", "/nada", {})[0] == 404
        assert call(port, "GET", "/nada")[0] == 404
    finally:
        stop(srv)


def test_seguridad_content_type_y_host():
    srv, port = start()
    try:
        # una pagina web maliciosa solo puede mandar text/plain sin permiso previo: se rechaza
        assert call(port, "POST", "/inject", raw='{"content": "x"}', headers={"Content-Type": "text/plain"})[0] == 415
        # DNS rebinding: el Host no es local
        assert call(port, "GET", "/health", headers={"Host": "evil.com"})[0] == 403
        assert call(port, "POST", "/inject", {"content": "x"}, headers={"Host": "evil.com:8765"})[0] == 403
        assert call(port, "GET", "/health", headers={"Host": f"localhost:{port}"})[0] == 200
    finally:
        stop(srv)


def test_token_opcional():
    srv, port = start(token="secreto123")
    try:
        assert call(port, "GET", "/health")[0] == 401
        assert call(port, "GET", "/health", headers={"Authorization": "Bearer malo"})[0] == 401
        assert call(port, "GET", "/health", headers={"Authorization": "Bearer secreto123"})[0] == 200
        st, r = call(port, "POST", "/inject", {"content": "dato con token valido y suficiente texto"}, headers={"Authorization": "Bearer secreto123"})
        assert st == 200, (st, r)
    finally:
        stop(srv)


if __name__ == "__main__":
    fallos = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as ex:
                fallos += 1
                print(f"FAIL  {name}: {type(ex).__name__}: {ex}")
    print("\nTODO OK" if not fallos else f"\n{fallos} prueba(s) fallaron")
    sys.exit(1 if fallos else 0)
