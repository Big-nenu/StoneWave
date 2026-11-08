"""Compara modelos con casos en español (necesita Ollama; no toca tu memoria: usa bases de datos temporales).

Tres pruebas, con los MISMOS prompts que usa el motor (mem_engine):
  decision   Contradicciones: ¿el modelo de decision detecta que un recuerdo anula a otro? (30 pares, tests/casos/pares_decision.jsonl)
             Se cuenta como contradiccion solo si lo dice en los dos ordenes, igual que el sueño.
  respaldo   ¿Un recuerdo respalda, contradice o no dice nada de una conclusion? (14 casos, tests/casos/respaldo_conclusiones.jsonl)
  generativo ¿Un modelo generativo escribe buenas conclusiones? (8 grupos con conclusion clara y 4 sin ninguna, tests/casos/casos_conclusiones.jsonl)
             Las conclusiones se verifican con el modelo de decision por defecto, igual que en el sueño.

Uso:
  python tests/compare_models.py                                  # todo, con los modelos por defecto
  python tests/compare_models.py decision d1:3b tev1:0.8b d3:2b   # solo decision y respaldo con estos modelos
  python tests/compare_models.py generativo qwen2.5:0.5b gemma4:e4b-it-qat
  python tests/compare_models.py todo --json resultados.json
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import mem_engine as me
from llm import Decider

CASOS = os.path.join(HERE, "casos")
DEFAULT_DECISION = list(dict.fromkeys([me.LLM_MODEL, "tev1:0.8b", "d3:2b"]))
DEFAULT_GEN = ["qwen2.5:0.5b", "lfm3:latest", "gemma4:e4b-it-qat", "qwen3.5:9b"]
_folders = []


def load(name):
    with open(os.path.join(CASOS, name), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def norm(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def make_engine(decision_model=None, gen_model=None):
    from llm import Generator
    folder = tempfile.mkdtemp()
    _folders.append(folder)
    return me.StoneWaveEngine(db_path=os.path.join(folder, "t.db"),
                              decider=Decider(model=decision_model) if decision_model else None,
                              generator=Generator(model=gen_model) if gen_model else None)


def pct(n, d):
    return f"{n}/{d}" if d else "-"


# ----------------------------------------------------------------------
# Decision: contradicciones
# ----------------------------------------------------------------------
def warm_decider(e):
    return e.decider.ask("ping", "ok", {"type": "choice", "instructions": "Elige una opcion.",
                                         "criteria": {"si": "Responder si", "no": "Responder no"}}, timeout=300) is not None


def run_decision(model, pairs):
    e = make_engine(decision_model=model)
    if not warm_decider(e):
        return {"model": model, "error": "el modelo no responde (¿instalado? ¿es un modelo de decision?)"}
    rows = []
    for p in pairs:
        t = time.perf_counter()
        first = e._ask_llm_conflict(p["a"], p["b"]) == "CONTRADICTORIO"
        flagged = first and e._ask_llm_conflict(p["b"], p["a"]) == "CONTRADICTORIO"  # como el sueño: en los dos ordenes
        rows.append({"id": p["id"], "tipo": p["tipo"], "una_vez": first, "marcado": flagged, "ms": (time.perf_counter() - t) * 1000})
    e.close()
    by = lambda tipo: [r for r in rows if r["tipo"] == tipo]
    return {"model": model, "rows": rows,
            "detecta": sum(r["marcado"] for r in by("contradice")), "n_contradice": len(by("contradice")),
            "falsos_complementa": sum(r["marcado"] for r in by("complementa")), "n_complementa": len(by("complementa")),
            "falsos_ambigua": sum(r["marcado"] for r in by("ambigua")), "n_ambigua": len(by("ambigua")),
            "falsos_con_un_orden": sum(r["una_vez"] for r in rows if r["tipo"] != "contradice"),
            "ms": sum(r["ms"] for r in rows) / len(rows)}


def run_support(model, cases):
    e = make_engine(decision_model=model)
    if not warm_decider(e):
        return {"model": model, "error": "el modelo no responde"}
    rows = []
    for c in cases:
        t = time.perf_counter()
        got = e._ask_llm_support(c["recuerdo"], c["conclusion"]).lower()
        rows.append({"id": c["id"], "esperado": c["esperado"], "obtenido": got, "ms": (time.perf_counter() - t) * 1000})
    e.close()
    ok = sum(r["esperado"] == r["obtenido"] for r in rows)
    # lo grave: aceptar como respaldo algo que no lo es (deja pasar una conclusion inventada)
    graves = [r["id"] for r in rows if r["obtenido"] == "respalda" and r["esperado"] != "respalda"]
    perdidos = [r["id"] for r in rows if r["esperado"] == "respalda" and r["obtenido"] != "respalda"]
    return {"model": model, "rows": rows, "aciertos": ok, "n": len(rows), "acepta_de_mas": graves, "rechaza_de_mas": perdidos,
            "ms": sum(r["ms"] for r in rows) / len(rows)}


# ----------------------------------------------------------------------
# Generativo: conclusiones
# ----------------------------------------------------------------------
def verifies(ev, texts, concl):
    """Misma regla que el sueño: la mayoria respalda y ninguno contradice."""
    verdicts = [ev._ask_llm_support(t, concl) for t in texts]
    return ("CONTRADICE" not in verdicts and verdicts.count("RESPALDA") >= max(2, math.ceil(len(texts) / 2))), verdicts


def run_generative(model, cases, ev):
    old = me.CONCLUSION_MODEL
    me.CONCLUSION_MODEL = model  # el motor lee este nombre al pedir una conclusion
    try:
        e = make_engine(gen_model=model)
        if e.generator.generate("Responde solo: hola", timeout=600, model=model) is None:
            return {"model": model, "error": "el modelo no responde (¿instalado?)"}
        rows = []
        for c in cases:
            t = time.perf_counter()
            concl = e._ask_llm_conclusion(c["recuerdos"])
            ms = (time.perf_counter() - t) * 1000
            row = {"id": c["id"], "tipo": c["tipo"], "texto": concl, "ms": ms}
            if concl is None:
                row["estado"] = "sin_respuesta"
            elif concl == "":
                row["estado"] = "vacia"
            else:
                row["pasa"], row["veredictos"] = verifies(ev, c["recuerdos"], concl)
                if norm(concl) in {norm(r) for r in c["recuerdos"]}:
                    row["estado"] = "copia"
                elif c["tipo"] == "clara":
                    row["estado"] = "acierta" if any(re.search(p, norm(concl)) for p in c["palabras"]) else "otra_cosa"
                else:
                    row["estado"] = "inventa"
            rows.append(row)
        e.close()
    finally:
        me.CONCLUSION_MODEL = old
    claras = [r for r in rows if r["tipo"] == "clara"]
    ningunas = [r for r in rows if r["tipo"] == "ninguna"]
    texts = [r["texto"] for r in rows if r.get("texto")]
    return {"model": model, "rows": rows,
            "acierta": sum(r["estado"] == "acierta" for r in claras), "acierta_y_pasa": sum(r["estado"] == "acierta" and r["pasa"] for r in claras),
            "n_claras": len(claras), "vacias": sum(r["estado"] == "vacia" for r in claras),
            "ninguna_ok": sum(r["estado"] == "vacia" for r in ningunas),
            "inventa": sum(r["estado"] == "inventa" for r in ningunas),
            "inventa_y_pasa": sum(r["estado"] == "inventa" and r["pasa"] for r in ningunas), "n_ningunas": len(ningunas),
            "largo_medio": sum(len(t) for t in texts) / len(texts) if texts else 0,
            "ms": sum(r["ms"] for r in rows) / len(rows)}


# ----------------------------------------------------------------------
# Reporte
# ----------------------------------------------------------------------
def print_decision(res):
    print(f"\n=== {res['model']} | contradicciones (en los dos ordenes) ===")
    if "error" in res:
        print("  " + res["error"])
        return
    print(f"  detecta las contradicciones: {pct(res['detecta'], res['n_contradice'])}")
    print(f"  falsos positivos (archivaria algo valido): complementarios {pct(res['falsos_complementa'], res['n_complementa'])}"
          f" | ambiguos {pct(res['falsos_ambigua'], res['n_ambigua'])}")
    print(f"  (con un solo orden habria marcado de mas: {res['falsos_con_un_orden']})   tiempo medio por par: {res['ms']:.0f} ms")
    miss = [r["id"] for r in res["rows"] if r["tipo"] == "contradice" and not r["marcado"]]
    fps = [r["id"] for r in res["rows"] if r["tipo"] != "contradice" and r["marcado"]]
    if miss:
        print("  no detecto: " + ", ".join(miss))
    if fps:
        print("  marco de mas: " + ", ".join(fps))


def print_support(res):
    print(f"\n=== {res['model']} | respaldo de conclusiones ===")
    if "error" in res:
        print("  " + res["error"])
        return
    print(f"  aciertos: {pct(res['aciertos'], res['n'])}   tiempo medio: {res['ms']:.0f} ms")
    print(f"  acepta de mas (deja pasar una conclusion mala): {len(res['acepta_de_mas'])} {res['acepta_de_mas'] or ''}")
    print(f"  rechaza de mas (tira una conclusion buena): {len(res['rechaza_de_mas'])} {res['rechaza_de_mas'] or ''}")


def print_generative(res):
    print(f"\n=== {res['model']} | conclusiones ===")
    if "error" in res:
        print("  " + res["error"])
        return
    print(f"  con conclusion clara: acierta {pct(res['acierta'], res['n_claras'])}, acierta y pasa la verificacion {pct(res['acierta_y_pasa'], res['n_claras'])},"
          f" dijo NINGUNA {res['vacias']}")
    print(f"  sin conclusion posible: dijo NINGUNA {pct(res['ninguna_ok'], res['n_ningunas'])}, invento {res['inventa']}"
          f" (y paso la verificacion: {res['inventa_y_pasa']})")
    print(f"  largo medio {res['largo_medio']:.0f} caracteres, tiempo medio {res['ms']:.0f} ms")
    for r in res["rows"]:
        mark = {"acierta": "OK", "vacia": "--" if r["tipo"] == "clara" else "OK", "inventa": "XX", "copia": "CP", "otra_cosa": "??", "sin_respuesta": "!!"}[r["estado"]]
        ver = "" if "pasa" not in r else (" [pasa]" if r["pasa"] else " [no pasa]")
        print(f"    {mark} {r['id']}{ver} ({r['ms']:.0f} ms) {(r['texto'] or '')[:140]}")


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Compara modelos de Ollama con casos en español")
    ap.add_argument("modo", nargs="?", default="todo", choices=["todo", "decision", "generativo"])
    ap.add_argument("modelos", nargs="*", help="modelos a probar (default: los de siempre)")
    ap.add_argument("--json", help="guarda los resultados en este archivo")
    args = ap.parse_args()
    if args.modelos and args.modo == "todo":
        ap.error("si das modelos, indica antes el modo: decision o generativo")
    out = {"decision": [], "respaldo": [], "generativo": []}
    try:
        if args.modo in ("todo", "decision"):
            pares, respaldo = load("pares_decision.jsonl"), load("respaldo_conclusiones.jsonl")
            for m in args.modelos or DEFAULT_DECISION:
                r = run_decision(m, pares)
                print_decision(r)
                out["decision"].append(r)
                r = run_support(m, respaldo)
                print_support(r)
                out["respaldo"].append(r)
        if args.modo in ("todo", "generativo"):
            casos = load("casos_conclusiones.jsonl")
            ev = make_engine(decision_model=me.LLM_MODEL)  # el verificador es el de siempre
            print(f"\n(verificador de las conclusiones: {me.LLM_MODEL})")
            for m in args.modelos or DEFAULT_GEN:
                r = run_generative(m, casos, ev)
                print_generative(r)
                out["generativo"].append(r)
            ev.close()
    finally:
        for f in _folders:
            shutil.rmtree(f, ignore_errors=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nResultados guardados en {args.json}")


if __name__ == "__main__":
    main()
