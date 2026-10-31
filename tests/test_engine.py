"""Pruebas del motor SIN Ollama: usa embeddings falsos (bolsa de palabras) y LLM falsos.
Ejecutar:  python tests/test_engine.py
"""
import os
import re
import sys
import json
import time
import sqlite3
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mem_engine import MemHolograficEngine
import mem_engine

mem_engine.CENTER_PRIOR = 10 ** 9  # sin centrado: los embeddings falsos no lo necesitan
# Umbrales para similitud coseno cruda (los embeddings falsos no estan centrados)
mem_engine.WEAK_MATCH, mem_engine.MIN_SIM, mem_engine.WAVE_MIN_SIM = 0.65, 0.5, 0.45
mem_engine.DUPLICATE_SIM, mem_engine.CONFLICT_SIM, mem_engine.RELATED_SIM = 0.95, 0.8, 0.6


def fake_embedding(text):
    v = [0.0] * 256
    for w in re.findall(r"\w+", text.lower()):
        if len(w) > 2:
            v[zlib.crc32(w.encode()) % 256] += 1.0
    return v


def make_engine(db=None, verdict="COMPLEMENTARIO"):
    db = db or os.path.join(tempfile.mkdtemp(), "t.db")
    e = MemHolograficEngine(db_path=db)
    e.get_embedding = fake_embedding
    e.get_embeddings = lambda texts: [fake_embedding(t) for t in texts]
    e.classify_topics = lambda t: ["otro"]
    e._ask_llm_conflict = lambda a, b: verdict
    return e


def rows(db, sql):
    with sqlite3.connect(db) as c:
        return c.execute(sql).fetchall()


def age(db, days, where="1=1"):
    with sqlite3.connect(db) as c:
        c.execute(f"UPDATE facts SET last_used=? WHERE {where}", (time.time() - days * 86400,))
        c.commit()


def test_inject_nuevo_y_refuerzo():
    e = make_engine()
    t = "Luis estudió ingeniería en computación en la universidad Lamar"
    assert "NUEVA" in e.inject(t)
    assert "REFUERZO" in e.inject(t)
    r = rows(e.db_path, "SELECT energy FROM facts")
    assert len(r) == 1 and abs(r[0][0] - 0.7) < 1e-6, r


def test_fijo_no_decae():
    e = make_engine()
    e.inject("Me llamo Luis Eduardo y vivo en Guadalajara", pinned=True)
    e.inject("El helado de vainilla es muy rico en verano", energy=0.5)
    age(e.db_path, 200)
    assert e.decay() == 1
    assert e.get_all_facts() == ["Me llamo Luis Eduardo y vivo en Guadalajara"]
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 1


def test_sotano_resucita():
    e = make_engine()
    e.inject("El helado de vainilla es muy rico en verano")
    e.inject("Luis trabaja en Neodata en Guadalajara")
    age(e.db_path, 200, "content LIKE '%helado%'")
    e.decay()
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 1
    res = e.query("helado de vainilla en verano")
    assert isinstance(res, list) and "helado" in res[0][3], res
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 0
    assert len(rows(e.db_path, "SELECT * FROM facts")) == 2


def test_sueno_contradiccion_archiva_el_viejo():
    e = make_engine(verdict="CONTRADICTORIO")
    e.inject("mi perro pequeño juega en el jardín todas las tardes")
    e.inject("mi perro pequeño juega en el jardín todas las mañanas")
    e.dream()
    activos = [r[0] for r in rows(e.db_path, "SELECT content FROM facts")]
    sotano = [r[0] for r in rows(e.db_path, "SELECT content FROM archived_facts")]
    assert activos == ["mi perro pequeño juega en el jardín todas las mañanas"], activos
    assert sotano == ["mi perro pequeño juega en el jardín todas las tardes"], sotano


def test_sueno_similares_no_se_fusionan():
    e = make_engine(verdict="COMPLEMENTARIO")
    a = "mi perro pequeño juega en el jardín todas las tardes"
    b = "mi perro pequeño juega en el jardín todas las mañanas"
    e.inject(a)
    e.inject(b)
    e.dream()
    assert sorted(e.get_all_facts()) == sorted([a, b])  # nada se reescribe ni se pierde
    assert [r[0] for r in rows(e.db_path, "SELECT kind FROM links")] == ["similar"]


def test_ondas_encuentran_el_recuerdo():
    e = make_engine()
    A = "Luis estudió ingeniería en computación en la universidad Lamar"
    e.inject(A)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    e.inject("Los gatos duermen mucho durante el día")
    res = e.query("qué estudió Luis en la universidad")
    assert isinstance(res, list) and res[0][3] == A, res
    assert e.query("astronomía de agujeros negros") == "No related memories found."


def test_cascada_por_enlaces():
    e = make_engine()
    A = "Luis estudió ingeniería en computación en la universidad Lamar"
    A2 = "Luis estudió ingeniería computación y se graduó en 2013"
    e.inject(A)
    e.inject(A2)
    e.inject("Los gatos duermen mucho durante el día")
    e.dream()
    assert [r[0] for r in rows(e.db_path, "SELECT kind FROM links")] == ["relacionado"]
    res = e.query("estudió luis universidad lamar")
    textos = [r[3] for r in res]
    assert textos[0] == A and A2 in textos, textos


def test_cache_se_actualiza():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    e1, e2 = make_engine(db), make_engine(db)
    e1.inject("Luis trabaja en Neodata en Guadalajara")
    assert e1.query("trabaja Neodata Guadalajara Luis")[0][3].startswith("Luis trabaja")
    e1.inject("Los gatos duermen mucho durante el día")           # misma instancia
    assert "gatos" in e1.query("gatos duermen mucho durante día")[0][3]
    e2.inject("El telescopio de mi vecino tiene una montura ecuatorial")   # otro proceso
    assert "telescopio" in e1.query("telescopio vecino montura ecuatorial")[0][3]


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
