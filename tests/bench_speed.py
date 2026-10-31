"""Mide la velocidad del motor con muchos recuerdos (sin Ollama).
Ejecutar:  python tests/bench_speed.py [N]
"""
import os
import sys
import json
import time
import random
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mem_engine import MemHolograficEngine

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
DIM = 768
random.seed(1)


def rvec():
    return [random.gauss(0, 1) for _ in range(DIM)]


db = os.path.join(tempfile.mkdtemp(), "bench.db")
e = MemHolograficEngine(db_path=db)
e.get_embedding = lambda t: rvec()
e.get_embeddings = lambda ts: [rvec() for _ in ts]
e.classify_topics = lambda t: ["otro"]
e._ask_llm_conflict = lambda a, b: "COMPLEMENTARIO"

with sqlite3.connect(db) as c:
    now = time.time()
    c.executemany(
        "INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned) VALUES ('default', ?, 0.5, ?, ?, 0)",
        [(f"recuerdo {i}", json.dumps(rvec()), now) for i in range(N)])
    c.commit()
print(f"{N} recuerdos, {DIM} dimensiones")


def timed(label, fn, times=1):
    t = time.perf_counter()
    for _ in range(times):
        fn()
    ms = (time.perf_counter() - t) / times * 1000
    print(f"{label:<45} {ms:9.1f} ms")


timed("query 1 (construye la cache)", lambda: e.query("pregunta de prueba sobre algo"))
timed("query siguientes (5 piedras, promedio de 20)", lambda: e.query("pregunta de prueba sobre algo"), 20)
timed("inject (con cache caliente)", lambda: e.inject("dato nuevo de prueba", namespace="default"))
timed("dream completo (decay + temas + enlaces)", lambda: e.dream())
