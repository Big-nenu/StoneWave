"""MemBench (esqueleto) para StoneWave: evalua el motor con casos de pregunta/respuesta esperada.

Cada caso hace lo mismo, en una base de datos temporal (no toca tu memoria):
    1. inyecta los recuerdos del caso (mas unos recuerdos de relleno, para que el corpus sea realista)
    2. envejece los que digan "age_days" (para probar el sotano)
    3. llama a dream()
    4. hace un query() con la pregunta
    5. compara lo que devolvio contra la respuesta esperada

Uso:
    python scripts/benchmark_stones.py                      # compara 1 piedra vs 5 piedras (necesita Ollama)
    python scripts/benchmark_stones.py --stones 5           # solo 5 piedras
    python scripts/benchmark_stones.py --cases casos.jsonl  # tus propios casos (uno por linea)
    python scripts/benchmark_stones.py --fake               # sin Ollama: solo valida que el benchmark corre
    python scripts/benchmark_stones.py --json salida.json   # guarda los resultados
    python scripts/benchmark_stones.py --min-pass 0.8       # codigo de salida 1 si pasa menos del 80 %
    python scripts/benchmark_stones.py --cases tests/membench_cases.jsonl --no-topics   # dataset grande (50 casos)
    python scripts/benchmark_stones.py --cases tests/membench_cases.jsonl --no-topics --shared   # todos los casos en UNA sola memoria

Formato de un caso (JSON, una linea por caso en el .jsonl):
    {"id": "multi_01", "category": "multi",
     "facts": ["texto", {"text": "otro texto", "pinned": false, "age_days": 200}],
     "question": "...?", "expected": ["fragmento1", "fragmento2"],
     "forbidden": ["no debe salir"], "dream": true, "requires_llm": false, "background": true,
     "expect_empty": false}

  expected   : fragmentos que DEBEN aparecer en los resultados (sin importar mayusculas ni acentos).
  forbidden  : fragmentos que NO deben aparecer (por ejemplo un dato ya contradicho).
  requires_llm: el caso necesita los modelos reales (contradicciones); se omite con --fake.
  expect_empty: la pregunta no tiene respuesta en la memoria; el caso pasa solo si el query no devuelve nada.
  Un caso pasa si aparecen todos los "expected" y ninguno de los "forbidden" en el top-3.
"""
import argparse
import gc
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
import zlib

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mem_engine as me

# Recuerdos de relleno: hacen que el corpus se parezca mas a una memoria real
BACKGROUND = [
    "El café de la esquina abre a las siete de la mañana",
    "La reunión semanal del equipo es los lunes",
    "Mi hermano estudia medicina en otra ciudad",
    "Compré un teclado mecánico nuevo para la computadora",
    "El domingo fuimos a la playa con toda la familia",
    "Mi plan de celular vence a fin de mes",
    "La biblioteca municipal tiene una sala de lectura muy tranquila",
    "Quiero aprender a tocar la guitarra este año",
]

# Casos de ejemplo (el dataset grande se arma despues; ver docs en la cabecera del archivo)
CASES = [
    {"id": "single_01", "category": "single",
     "facts": ["Me llamo Ana García", "Trabajo en una panadería en Zapopan", "Mi color favorito es el verde"],
     "question": "¿Cómo me llamo?", "expected": ["Ana García"]},
    {"id": "multi_01", "category": "multi",
     "facts": ["Estudié Ingeniería Civil en la UdeG", "Trabajo en una constructora en Guadalajara",
               "Me gusta el ciclismo de montaña", "Tengo un perro llamado Bruno"],
     "question": "¿Qué sé de mi trabajo y mis estudios?", "expected": ["UdeG", "constructora"]},
    {"id": "contradiccion_01", "category": "contradiction", "requires_llm": True,
     "facts": ["Vivo en Guadalajara con mis papás", "Ahora vivo en Monterrey con mi pareja"],
     "question": "¿Dónde vivo?", "expected": ["Monterrey"], "forbidden": ["Guadalajara"]},
    {"id": "sotano_01", "category": "basement",
     "facts": [{"text": "El proyecto del año pasado se llamaba Colibrí y era una app de recetas", "age_days": 200},
               "Este año quiero hacer una app de finanzas personales"],
     "question": "¿Te acuerdas del proyecto del año pasado?", "expected": ["Colibrí"]},
]


# ----------------------------------------------------------------------
# Evaluacion
# ----------------------------------------------------------------------
def normalize(text):
    """Minusculas y sin acentos, para comparar fragmentos."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def evaluate(expected, forbidden, results):
    """Compara los textos devueltos (top-k) contra lo esperado. Devuelve un dict con el veredicto."""
    texts = [normalize(t) for t in results]
    joined = " || ".join(texts)
    hits = [e for e in expected if normalize(e) in joined]
    missed = [e for e in expected if e not in hits]
    bad = [f for f in (forbidden or []) if any(normalize(f) in t for t in texts)]
    coverage = len(hits) / len(expected) if expected else 1.0
    return {"passed": not missed and not bad, "coverage": coverage, "hits": hits, "missed": missed, "forbidden_found": bad}


# ----------------------------------------------------------------------
# Motor falso (--fake): bolsa de palabras, sin Ollama. Solo sirve para probar el benchmark en si.
# ----------------------------------------------------------------------
def _fake_embedding(text):
    v = [0.0] * 256
    for w in re.findall(r"\w+", text.lower()):
        if len(w) > 2:
            v[zlib.crc32(w.encode()) % 256] += 1.0
    return v


FAKE_THRESHOLDS = {"CENTER_PRIOR": 10 ** 9, "WEAK_MATCH": 0.65, "MIN_SIM": 0.5, "WAVE_MIN_SIM": 0.45,
                   "DUPLICATE_SIM": 0.95, "CONFLICT_SIM": 0.8, "RELATED_SIM": 0.6}


def _make_engine(db_path, fake, no_topics=False):
    e = me.StoneWaveEngine(db_path=db_path)
    if fake:
        e.get_embedding = _fake_embedding
        e.get_embeddings = lambda texts: [_fake_embedding(t) for t in texts]
        e._ask_llm_conflict = lambda a, b: "COMPLEMENTARIO"
    if fake or no_topics:  # los temas no influyen en la consulta: saltarlos ahorra ~1.5 s por recuerdo en cada sueño
        e.classify_topics = lambda t: ["otro"]
    return e


# ----------------------------------------------------------------------
# Ejecucion de un caso
# ----------------------------------------------------------------------
def _norm_fact(f):
    return {"text": f, "pinned": False, "age_days": 0} if isinstance(f, str) else \
        {"text": f["text"], "pinned": bool(f.get("pinned", False)), "age_days": float(f.get("age_days", 0))}


def run_case(case, stones=5, fake=False, top_k=3, no_topics=False):
    """Inyecta -> dream -> query -> compara. Devuelve un dict con el resultado (nunca lanza excepciones)."""
    base = {"id": case["id"], "category": case.get("category", "general"), "stones": stones,
            "question": case["question"], "expected": case["expected"]}
    if fake and case.get("requires_llm"):
        return {**base, "skipped": True, "passed": None, "note": "necesita los modelos reales (no corre con --fake)"}
    folder = tempfile.mkdtemp()
    engine = None
    old_stones = me.STONES
    try:
        me.STONES = stones
        engine = _make_engine(os.path.join(folder, "bench.db"), fake, no_topics)
        facts = [_norm_fact(f) for f in case["facts"]]
        if case.get("background", True):
            for text in BACKGROUND:
                engine.inject(text)
        for f in facts:
            msg = engine.inject(f["text"], pinned=f["pinned"])
            if "ERROR" in msg:
                return {**base, "skipped": False, "passed": False, "error": msg}
        for f in facts:  # envejecer (para probar decaimiento y sotano)
            if f["age_days"]:
                with sqlite3.connect(engine.db_path) as c:
                    c.execute("UPDATE facts SET last_used=? WHERE content=?", (time.time() - f["age_days"] * 86400, f["text"]))
                    c.commit()
        if case.get("dream", True):
            engine.dream()
        t = time.perf_counter()
        res = engine.query(case["question"])
        ms = (time.perf_counter() - t) * 1000
        if isinstance(res, str):
            returned, top = [], []
        else:
            returned = [r[3] for r in res[:me.RESULTS]]
            top = [(round(float(r[0]), 3), r[3]) for r in res[:me.RESULTS]]
        verdict = evaluate(case["expected"], case.get("forbidden"), returned)
        if case.get("expect_empty"):  # pregunta sin respuesta: lo correcto es NO devolver nada (no inventar)
            verdict = {"passed": not returned, "coverage": 0.0 if returned else 1.0, "hits": [], "missed": [],
                       "forbidden_found": [returned[0][:60]] if returned else []}
        return {**base, "skipped": False, **verdict, "top": top, "ms": ms,
                "note": res if isinstance(res, str) else ""}
    except Exception as ex:
        return {**base, "skipped": False, "passed": False, "error": f"{type(ex).__name__}: {ex}"}
    finally:
        me.STONES = old_stones
        if engine:
            engine.close()
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)


def run_shared(cases, stones=5, fake=False, top_k=3, no_topics=False):
    """Todos los casos en UNA sola memoria (decenas de recuerdos mezclados), como en el uso real.

    Inyecta todo, sueña una vez y hace cada pregunta. Quedan fuera las contradicciones y el sotano
    (sus recuerdos chocarian con los de otros casos, o necesitan envejecer).
    """
    eligible = [c for c in cases if c.get("category") not in ("contradiction", "basement") and not c.get("requires_llm")
                and not any(isinstance(f, dict) and f.get("age_days") for f in c["facts"])]
    folder = tempfile.mkdtemp()
    engine = None
    old_stones = me.STONES
    out = []
    try:
        me.STONES = stones
        engine = _make_engine(os.path.join(folder, "shared.db"), fake, no_topics)
        for text in BACKGROUND:
            engine.inject(text)
        for c in eligible:
            for f in (_norm_fact(x) for x in c["facts"]):
                engine.inject(f["text"], pinned=f["pinned"])
        with sqlite3.connect(engine.db_path) as conn:
            n_mem = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        engine.dream()
        for c in eligible:
            base = {"id": c["id"], "category": c.get("category", "general"), "stones": stones, "question": c["question"],
                    "expected": c["expected"], "shared": True, "memories": n_mem}
            try:
                t = time.perf_counter()
                res = engine.query(c["question"])
                ms = (time.perf_counter() - t) * 1000
                if isinstance(res, str):
                    returned, top = [], []
                else:
                    returned = [r[3] for r in res[:me.RESULTS]]
                    top = [(round(float(r[0]), 3), r[3]) for r in res[:me.RESULTS]]
                verdict = evaluate(c["expected"], c.get("forbidden"), returned)
                if c.get("expect_empty"):
                    verdict = {"passed": not returned, "coverage": 0.0 if returned else 1.0, "hits": [], "missed": [],
                               "forbidden_found": [returned[0][:60]] if returned else []}
                out.append({**base, "skipped": False, **verdict, "top": top, "ms": ms, "note": res if isinstance(res, str) else ""})
            except Exception as ex:
                out.append({**base, "skipped": False, "passed": False, "error": f"{type(ex).__name__}: {ex}"})
    except Exception as ex:
        out.append({"id": "shared", "category": "general", "stones": stones, "question": "", "expected": [], "skipped": False,
                    "passed": False, "error": f"{type(ex).__name__}: {ex}"})
    finally:
        me.STONES = old_stones
        if engine:
            engine.close()
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)
    return out


class OllamaDown(Exception):
    """Ollama dejo de responder a mitad del benchmark. 'results' trae lo que alcanzo a medirse."""
    def __init__(self, results):
        super().__init__("Ollama dejo de responder")
        self.results = results


def _ollama_up():
    try:
        return requests.get("http://localhost:11434/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _failed_by_ollama(r, fake):
    """True si el caso fallo por un error (no por una respuesta mala) y Ollama ya no contesta."""
    if fake or r.get("skipped") or not (r.get("error") or "No embedding" in str(r.get("note", ""))):
        return False
    return not _ollama_up()


def _progress(n, total, stones, r):
    estado = "omitido" if r.get("skipped") else ("OK" if r.get("passed") else "FALLA")
    ms = f" {r['ms']:.0f} ms" if "ms" in r else ""
    print(f"[{n}/{total}] {stones} piedra(s) {r['id']}: {estado}{ms}", flush=True)


def run_benchmark(cases, stones_list=(1, 5), fake=False, no_topics=False, shared=False):
    """Corre todos los casos mostrando el avance. Si Ollama deja de responder, lanza OllamaDown con lo medido."""
    results, n = [], 0
    if shared:
        total = len(cases) * len(stones_list)
        for s in stones_list:
            print(f"[{s} piedra(s)] modo compartido: guardando y sonando (puede tardar)...", flush=True)
            for r in run_shared(cases, stones=s, fake=fake, no_topics=no_topics):
                n += 1
                results.append(r)
                _progress(n, total, s, r)
                if _failed_by_ollama(r, fake):
                    raise OllamaDown(results)
        return results
    total = len(cases) * len(stones_list)
    for s in stones_list:
        for c in cases:
            n += 1
            r = run_case(c, stones=s, fake=fake, no_topics=no_topics)
            results.append(r)
            _progress(n, total, s, r)
            if _failed_by_ollama(r, fake):
                raise OllamaDown(results)
    return results


# ----------------------------------------------------------------------
# Reporte
# ----------------------------------------------------------------------
def summarize(results, stones_list):
    print("\n=== MemBench: resumen ===")
    print(f"{'piedras':<9}{'casos':<8}{'pasan':<8}{'% acierto':<11}{'cobertura':<11}{'ms medio':<9}")
    summary = {}
    for s in stones_list:
        rs = [r for r in results if r["stones"] == s and not r.get("skipped")]
        if not rs:
            continue
        passed = sum(1 for r in rs if r["passed"])
        cov = sum(r.get("coverage", 0.0) for r in rs) / len(rs)
        times = [r["ms"] for r in rs if "ms" in r]
        avg = sum(times) / len(times) if times else 0.0
        summary[s] = passed / len(rs)
        print(f"{s:<9}{len(rs):<8}{passed:<8}{passed / len(rs) * 100:<11.0f}{cov * 100:<11.0f}{avg:<9.0f}")
    skipped = sorted({r["id"] for r in results if r.get("skipped")})
    if skipped:
        print(f"\nOmitidos (necesitan los modelos reales): {', '.join(skipped)}")
    cats = sorted({r["category"] for r in results if not r.get("skipped")})
    if len(cats) > 1:
        print("\nPor categoria (pasan/total):")
        for cat in cats:
            parts = []
            for s in stones_list:
                rs = [r for r in results if r["stones"] == s and r["category"] == cat and not r.get("skipped")]
                if rs:
                    parts.append(f"{s} piedra(s): {sum(1 for r in rs if r['passed'])}/{len(rs)}")
            print(f"  {cat:<15} " + "  |  ".join(parts))
    fails = [r for r in results if not r.get("skipped") and not r["passed"]]
    if fails:
        print("\nFallos:")
        for r in fails:
            why = r.get("error") or (f"faltan {r['missed']}" if r.get("missed") else "") + \
                (f" y aparecio lo prohibido {r['forbidden_found']}" if r.get("forbidden_found") else "")
            top1 = r["top"][0][1][:50] if r.get("top") else (r.get("note") or "sin resultados")
            print(f"  [{r['stones']} piedra(s)] {r['id']}: {why.strip()} | primero: {top1}")
    return summary


def load_cases(path):
    cases = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                case = json.loads(line)
                for key in ("id", "facts", "question", "expected"):
                    if key not in case:
                        raise ValueError(f"{path}:{n}: falta el campo '{key}'")
                cases.append(case)
    return cases


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="MemBench (esqueleto) de StoneWave")
    ap.add_argument("--stones", default="1,5", help="numero(s) de piedras a comparar, separados por coma (default: 1,5)")
    ap.add_argument("--cases", help="archivo .jsonl con casos (default: los casos de ejemplo)")
    ap.add_argument("--fake", action="store_true", help="sin Ollama: embeddings falsos, solo valida que el benchmark corre")
    ap.add_argument("--no-background", action="store_true", help="no agregar recuerdos de relleno")
    ap.add_argument("--no-topics", action="store_true", help="no clasificar temas en el sueño (no afecta a la consulta; hace la corrida mucho mas rapida)")
    ap.add_argument("--shared", action="store_true", help="todos los casos en UNA sola memoria (mas realista; omite contradicciones y sotano)")
    ap.add_argument("--set", action="append", default=[], metavar="NOMBRE=VALOR",
                    help="cambia una constante de mem_engine solo durante la corrida (ej. --set MIN_SIM=0.3 --set RESULTS=5); se puede repetir")
    ap.add_argument("--json", help="guarda los resultados en este archivo")
    ap.add_argument("--min-pass", type=float, default=0.0, help="si la tasa de acierto (0 a 1) con el mayor numero de piedras es menor, sale con codigo 1")
    args = ap.parse_args()

    stones_list = [int(x) for x in args.stones.split(",") if x.strip()]
    cases = load_cases(args.cases) if args.cases else [dict(c) for c in CASES]
    if args.no_background:
        for c in cases:
            c["background"] = False

    overrides = {}
    for item in args.set:
        name, _, raw = item.partition("=")
        if not hasattr(me, name) or not raw:
            print(f"--set {item}: no existe esa constante en mem_engine (formato NOMBRE=VALOR)", file=sys.stderr)
            return 2
        overrides[name] = type(getattr(me, name))(raw)
    saved = {k: getattr(me, k) for k in list(FAKE_THRESHOLDS) + list(overrides)}
    for k, v in overrides.items():
        setattr(me, k, v)
    if args.fake:
        for k, v in FAKE_THRESHOLDS.items():
            setattr(me, k, v)
        print("MODO --fake: embeddings falsos; los numeros NO miden la calidad del motor, solo prueban el benchmark.")
    else:
        probe = me.StoneWaveEngine(db_path=os.path.join(tempfile.mkdtemp(), "probe.db"))
        ok = probe.get_embedding("ping") is not None
        probe.close()
        if not ok:
            print("No se pudo obtener un embedding de Ollama. Revisa con:  python mem_cli.py doctor   (o usa --fake)", file=sys.stderr)
            return 2
    print(f"{len(cases)} caso(s) x piedras {stones_list} | corpus: {'sin relleno' if args.no_background else f'{len(BACKGROUND)} recuerdos de relleno'}")
    aborted = False
    try:
        results = run_benchmark(cases, stones_list, fake=args.fake, no_topics=args.no_topics, shared=args.shared)
    except OllamaDown as ex:
        results, aborted = ex.results, True
        print("\nOLLAMA DEJO DE RESPONDER: se detuvo el benchmark. Revisa con:  python mem_cli.py doctor\n"
              "Abajo va lo medido hasta ahora (incompleto, no lo uses para comparar).", file=sys.stderr)
    finally:
        for k, v in saved.items():
            setattr(me, k, v)
    summary = summarize(results, stones_list)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nResultados guardados en {args.json}")
    best = summary.get(max(stones_list))
    if aborted:
        return 2
    return 1 if (best is not None and best < args.min_pass) else 0


if __name__ == "__main__":
    sys.exit(main())
