"""Prueba con Ollama REAL (usa una base de datos temporal, no toca tu memoria).
Ejecutar:  python tests/real_test.py
"""
import os
import sys
import time
import sqlite3
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mem_engine
from mem_engine import MemHolograficEngine

FACTS = [
    "Me llamo Luis Eduardo Hernández Vargas",
    "Estudié Ingeniería en Computación en Lamar de 2009 a 2013",
    "Trabajo en Neodata en Guadalajara organizando cursos de capacitación",
    "Vivo en Guadalajara con mis papás y mi hermana",
    "Estoy desarrollando un juego web llamado Atom-Fusion con Cline",
    "Mi perfume favorito es Versace Oud Noir",
    "Me gusta aprender haciendo ejercicios prácticos, no solo leyendo",
    "Tengo un Kindle con jailbreak para usar KOReader",
    "El efecto fotoeléctrico demuestra que la luz tiene partículas llamadas fotones",
    "La luz también se comporta como onda en el experimento de la doble rendija",
    "El bosón de Higgs da masa a las partículas elementales",
    "Uso Ollama con modelos locales para mis proyectos de IA",
]
# (pregunta, [textos que cuentan como acierto])
QUERIES = [
    ("¿Cómo me llamo?", ["Luis Eduardo"]),
    ("¿Qué estudié y dónde?", ["Lamar"]),
    ("¿Dónde trabajo?", ["Neodata"]),
    ("¿Con quién vivo?", ["papás"]),
    ("¿En qué videojuego estoy trabajando?", ["Atom-Fusion"]),
    ("¿Qué perfume me gusta?", ["Oud Noir"]),
    ("¿Cómo prefiero estudiar?", ["haciendo"]),
    ("¿Qué dispositivo tengo para leer?", ["Kindle"]),
    ("¿La luz es onda o partícula?", ["fotones", "doble rendija"]),
    ("¿Qué le da masa a las partículas?", ["Higgs"]),
    ("¿Qué herramientas de IA local uso?", ["Ollama"]),
    ("¿Cuál es mi comida favorita?", []),   # no hay respuesta: no debe inventar
]


MULTI = [
    ("¿Qué sé de mi trabajo y mis estudios?", ["Neodata", "Lamar"]),
    ("¿Qué proyectos de programación y de IA tengo?", ["Atom-Fusion", "Ollama"]),
    ("¿Qué sabes de la física de la luz?", ["fotones", "doble rendija"]),
    ("Cuéntame de mi vida personal: dónde vivo y qué me gusta", ["Guadalajara", "perfume"]),
]


def run_multi(e, label):
    """Preguntas que necesitan 2 recuerdos: cuenta cuantos de los 2 aparecen en el top-3."""
    got = total = 0
    for q, need in MULTI:
        res = e.query(q)
        texts = [r[3] for r in res] if isinstance(res, list) else []
        got += sum(any(k in x for x in texts) for k in need)
        total += len(need)
    print(f"  Cobertura de preguntas de varios hechos ({label}): {got}/{total}")


def ms(t):
    return (time.perf_counter() - t) * 1000


def run_queries(e, label, verbose=True):
    top1 = top3 = 0
    total = sum(1 for _, ok in QUERIES if ok)
    times = []
    print(f"\n=== Consultas ({label}) ===")
    for q, ok in QUERIES:
        t = time.perf_counter()
        res = e.query(q)
        dt = ms(t)
        times.append(dt)
        if isinstance(res, str):
            if verbose or ok:
                print(f"  {'--' if not ok else 'XX'} {dt:6.0f} ms | {q}  ->  {res}")
            continue
        hit1 = bool(ok) and any(k in res[0][3] for k in ok)
        hit3 = bool(ok) and any(any(k in r[3] for k in ok) for r in res)
        top1 += hit1
        top3 += hit3
        mark = "OK" if hit1 else ("~3" if hit3 else ("--" if not ok else "XX"))
        if verbose or mark in ("XX", "~3"):
            print(f"  {mark} {dt:6.0f} ms | {q}  ->  [{res[0][0]:.2f}] {res[0][3][:60]}")
    print(f"  Acierto top-1: {top1}/{total}   top-3: {top3}/{total}   tiempo medio: {sum(times)/len(times):.0f} ms")
    return top1, top3


def main():
    db = os.path.join(tempfile.mkdtemp(), "real.db")
    e = MemHolograficEngine(db_path=db)
    print(f"Modelos: embeddings={mem_engine.EMBEDDING_MODEL}  decision={mem_engine.LLM_MODEL}  sintesis={mem_engine.SYNTHESIS_MODEL}")

    t = time.perf_counter(); v = e.get_embedding("hola"); print(f"Embedding unico: {ms(t):.0f} ms, dimension {len(v) if v else None}")
    t = time.perf_counter(); vs = e.get_embeddings(["uno", "dos", "tres", "cuatro", "cinco"]); print(f"5 embeddings en lote: {ms(t):.0f} ms")
    if not v:
        print("SIN EMBEDDINGS: revisa Ollama y el modelo"); return

    print("\n=== Guardar ===")
    t = time.perf_counter()
    for f in FACTS:
        e.inject(f)
    print(f"{len(FACTS)} recuerdos en {ms(t):.0f} ms ({ms(t)/len(FACTS):.0f} ms c/u)")

    # Calibracion: similitud del recuerdo correcto vs el mejor incorrecto
    print("\n=== Calibracion (similitud cruda) ===")
    ids, mat, idx, mu = e._load_vectors("default", "facts")
    texts = {r[0]: r[1] for r in sqlite3.connect(db).execute("SELECT id, content FROM facts")}
    good, bad = [], []
    for q, ok in QUERIES:
        if not ok:
            qv = e.get_embedding(q); _, sims = e._sims(qv, "default")
            print(f"  (sin respuesta) mejor sim = {max(sims):.2f}  | {q}")
            continue
        qv = e.get_embedding(q); _, sims = e._sims(qv, "default")
        g = max(float(s) for i, s in zip(ids, sims) if any(k in texts[i] for k in ok))
        b = max(float(s) for i, s in zip(ids, sims) if not any(k in texts[i] for k in ok))
        good.append(g); bad.append(b)
        print(f"  correcto {g:.2f} | mejor incorrecto {b:.2f} | {q}")
    print(f"  Correctos: min {min(good):.2f} media {sum(good)/len(good):.2f}   Incorrectos: max {max(bad):.2f} media {sum(bad)/len(bad):.2f}")

    print("\n=== Calibracion de pares (similitud en el espacio del motor) ===")
    PAIRS = [
        ("duplicado", "Me llamo Luis Eduardo Hernández Vargas", "Mi nombre es Luis Eduardo Hernández Vargas"),
        ("duplicado", "Tengo un Kindle con jailbreak para usar KOReader", "Mi Kindle tiene jailbreak y uso KOReader"),
        ("contradice", "Vivo en Guadalajara con mis papás y mi hermana", "Ahora vivo en Monterrey con mi pareja"),
        ("contradice", "Mi perfume favorito es Versace Oud Noir", "Mi perfume favorito es Drakkar Noir"),
        ("complementa", FACTS[8], FACTS[9]),
        ("complementa", FACTS[2], "En Neodata doy cursos los sábados"),
        ("relacionado", FACTS[1], FACTS[11]),
        ("sin relacion", FACTS[5], FACTS[10]),
        ("sin relacion", FACTS[3], FACTS[8]),
    ]
    for kind, a, b in PAIRS:
        ua, ub = e._unit(np.asarray(e.get_embedding(a), dtype=np.float32), mu), e._unit(np.asarray(e.get_embedding(b), dtype=np.float32), mu)
        print(f"  {kind:<13} sim {float(ua @ ub):.2f} | {a[:34]} / {b[:34]}")

    run_queries(e, "5 piedras, angulos = palabras clave")
    run_multi(e, "5 piedras, palabras clave")
    saved = (mem_engine.STONES, mem_engine.STONES_LLM, mem_engine.ANGLE_WEIGHT)
    for label, st, llm, w in [("1 piedra (sin ondas)", 1, False, 0.5),
                              ("5 piedras, palabras clave, peso 0.25", 5, False, 0.25),
                              ("5 piedras, angulos inventados por LLM", 5, True, 0.5)]:
        mem_engine.STONES, mem_engine.STONES_LLM, mem_engine.ANGLE_WEIGHT = st, llm, w
        run_queries(e, label, verbose=False)
        if not llm:
            run_multi(e, label)
    mem_engine.STONES, mem_engine.STONES_LLM, mem_engine.ANGLE_WEIGHT = saved

    # Contradiccion y sueño
    print("\n=== Sueño ===")
    e.inject("Ahora vivo en Monterrey con mi pareja")
    a, b = "Vivo en Guadalajara con mis papás y mi hermana", "Ahora vivo en Monterrey con mi pareja"
    va, vb = e.get_embedding(a), e.get_embedding(b)
    print(f"Sim Guadalajara/Monterrey = {e.calc_sim(va, vb):.2f} (el sueño solo revisa si >= {mem_engine.CONFLICT_SIM})")
    t = time.perf_counter(); print("tev dice (contradictorio esperado):", e._ask_llm_conflict(a, b), f"[{ms(t):.0f} ms]")
    c1, c2 = FACTS[8], FACTS[9]
    print(f"Sim foton/onda = {e.calc_sim(e.get_embedding(c1), e.get_embedding(c2)):.2f}")
    print("tev dice (complementario esperado):", e._ask_llm_conflict(c1, c2))
    t = time.perf_counter(); print(e.dream()); print(f"dream: {ms(t):.0f} ms")
    print("Enlaces:", sqlite3.connect(db).execute("SELECT fact_a, fact_b, round(sim,2), kind FROM links").fetchall())
    print("Sotano:", [r[0] for r in sqlite3.connect(db).execute("SELECT content FROM archived_facts")])
    print("Temas:", sqlite3.connect(db).execute("SELECT t.topic_name, count(*) FROM fact_topics f JOIN topics t ON t.id=f.topic_id GROUP BY 1").fetchall())

    # Sotano
    print("\n=== Sótano ===")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE facts SET last_used=? WHERE content LIKE '%Kindle%'", (time.time() - 200 * 86400,)); c.commit()
    print("decay archivo:", e.decay(), "recuerdo(s)")
    res = e.query("¿Qué dispositivo tengo para leer?")
    print("Consulta sobre lo archivado ->", res if isinstance(res, str) else f"[{res[0][0]:.2f}] {res[0][3]}")
    print("Sotano ahora:", sqlite3.connect(db).execute("SELECT count(*) FROM archived_facts").fetchone()[0], "| Biblioteca:", len(e.get_all_facts()))
    e.close()


if __name__ == "__main__":
    main()
