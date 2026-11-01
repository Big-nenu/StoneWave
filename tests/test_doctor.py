"""Pruebas del Doctor (solucionador) SIN Ollama: se simula Ollama con respuestas falsas.
Ejecutar:  python tests/test_doctor.py
"""
import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import make_engine
import doctor
import mem_engine as me

ALL_MODELS = [me.EMBEDDING_MODEL, me.LLM_MODEL, me.SYNTHESIS_MODEL]


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


class FakeRequests:
    """Reemplaza a 'requests' dentro de doctor para simular a Ollama."""
    def __init__(self, models=None, down=False, embed_status=200, llm_status=200):
        self.models = ALL_MODELS if models is None else models
        self.down, self.embed_status, self.llm_status = down, embed_status, llm_status

    def get(self, url, **kw):
        if self.down:
            raise ConnectionError("Ollama apagado")
        return FakeResp(200, {"models": [{"name": m} for m in self.models]})

    def post(self, url, **kw):
        if url == me.OLLAMA_EMBED_URL:
            return FakeResp(self.embed_status, {"embeddings": [[0.1, 0.2, 0.3]]})
        return FakeResp(self.llm_status, {"answers": {}})


def healthy_db():
    folder = tempfile.mkdtemp()
    db = os.path.join(folder, "memoria.db")
    e = make_engine(db)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    e.dream()
    e.close()
    return db


def run(db, fake):
    """Corre el doctor contra 'db' con Ollama simulado. Devuelve (codigo, texto)."""
    old_env = os.environ.get("STONEWAVE_DB")
    old_req = doctor.requests
    os.environ["STONEWAVE_DB"] = db
    doctor.requests = fake
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = doctor.run_doctor("default")
    finally:
        doctor.requests = old_req
        if old_env is None:
            os.environ.pop("STONEWAVE_DB", None)
        else:
            os.environ["STONEWAVE_DB"] = old_env
    return rc, buf.getvalue()


def test_todo_sano_no_pide_soluciones():
    rc, out = run(healthy_db(), FakeRequests())
    assert rc == 0 and "SOLUCIONES SUGERIDAS" not in out and "Todo en orden" in out, out


def test_ollama_caido():
    rc, out = run(healthy_db(), FakeRequests(down=True))
    assert rc == 1 and "SOLUCIONES SUGERIDAS" in out, out
    assert "ollama serve" in out and "api/tags" in out, out


def test_modelo_faltante_da_el_ollama_pull():
    faltante = me.LLM_MODEL
    rc, out = run(healthy_db(), FakeRequests(models=[me.EMBEDDING_MODEL, me.SYNTHESIS_MODEL]))
    assert rc == 1 and f"$ ollama pull {faltante}" in out and "SOLUCIONES SUGERIDAS" in out, out


def test_modelo_instalado_pero_sin_respuesta():
    rc, out = run(healthy_db(), FakeRequests(llm_status=404))
    assert rc == 1 and "ollama stop" in out and "actualiza Ollama" in out, out  # el 404 da una pista extra
    rc, out = run(healthy_db(), FakeRequests(embed_status=500))
    assert rc == 1 and f"ollama stop {me.EMBEDDING_MODEL}" in out, out


def test_base_de_datos_danada_con_respaldo():
    good = healthy_db()
    bad = os.path.join(tempfile.mkdtemp(), "memoria.db")
    with open(bad, "wb") as f:
        f.write(b"esto no es una base de datos sqlite" * 50)
    shutil.copy2(good, bad + ".bak-embeddings")
    rc, out = run(bad, FakeRequests())
    assert rc == 1 and "SOLUCIONES SUGERIDAS" in out, out
    assert ".bak-embeddings" in out and (".danada" in out), out  # primero guarda copia, luego restaura el respaldo


def test_base_de_datos_danada_sin_respaldo_sugiere_recover():
    bad = os.path.join(tempfile.mkdtemp(), "memoria.db")
    with open(bad, "wb") as f:
        f.write(b"basura" * 100)
    rc, out = run(bad, FakeRequests())
    assert rc == 1 and ".recover" in out, out


def test_faltan_tablas():
    db = os.path.join(tempfile.mkdtemp(), "memoria.db")
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE otra_cosa (a INTEGER)")
        c.commit()
    rc, out = run(db, FakeRequests())
    assert rc == 1 and "StoneWaveEngine().close()" in out, out


def test_sin_numpy_es_aviso_no_grave():
    saved = me.np
    me.np = None
    try:
        rc, out = run(healthy_db(), FakeRequests())
    finally:
        me.np = saved
    assert rc == 0 and "RECOMENDACIONES" in out and "pip install numpy" in out and "SOLUCIONES SUGERIDAS" not in out, out


if __name__ == "__main__":
    fallos = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as ex:
                fallos += 1
                print(f"FAIL  {name}: {type(ex).__name__}: {str(ex)[:600]}")
    print("\nTODO OK" if not fallos else f"\n{fallos} prueba(s) fallaron")
    sys.exit(1 if fallos else 0)
