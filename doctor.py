"""StoneWave Doctor: reporte rapido de salud de la memoria (solo lectura, no modifica nada).

Uso:  python mem_cli.py doctor [-n NAMESPACE]
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import mem_engine as me

OK, WARN, BAD = "[OK]", "[!!]", "[XX]"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _q(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return None


def _scalar(conn, sql, args=()):
    rows = _q(conn, sql, args)
    return rows[0][0] if rows else None


def _installed(model, names):
    return model in names or f"{model}:latest" in names


def _ping_embed():
    t = time.perf_counter()
    try:
        r = requests.post(me.OLLAMA_EMBED_URL, json={"model": me.EMBEDDING_MODEL, "input": me.QUERY_PREFIX + "ping"}, timeout=30)
        embs = r.json().get("embeddings") if r.status_code == 200 else None
        ok = bool(embs and embs[0])
        return ok, (time.perf_counter() - t) * 1000, (f"vectores de {len(embs[0])} dimensiones" if ok else f"HTTP {r.status_code}")
    except Exception as ex:
        return False, (time.perf_counter() - t) * 1000, str(ex)[:70]


def _ping_llm():
    t = time.perf_counter()
    payload = {"model": me.LLM_MODEL, "state": "ping",
               "questions": {"ok": {"type": "choice", "instructions": "Elige una opcion.",
                                    "criteria": {"si": "Responder si", "no": "Responder no"}}}}
    try:
        r = requests.post(me.OLLAMA_SYSTEMONE_URL, json=payload, timeout=60)
        ok = r.status_code == 200 and "answers" in r.json()
        return ok, (time.perf_counter() - t) * 1000, ("responde" if ok else f"HTTP {r.status_code}")
    except Exception as ex:
        return False, (time.perf_counter() - t) * 1000, str(ex)[:70]


def run_doctor(namespace="default"):
    """Imprime el reporte. Devuelve 0 si todo esta bien (o solo avisos) y 1 si hay algun problema grave."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    bad = 0

    def line(status, text):
        nonlocal bad
        if status == BAD:
            bad += 1
        print(f"  {status} {text}")

    print("=== StoneWave Doctor ===")

    # ---------------- Base de datos ----------------
    print("\nBase de datos")
    custom = me.env_var("DB")
    db = Path(custom) if custom else me.DEFAULT_MEM_FOLDER / "memoria.db"
    print(f"  Ruta: {db}")
    conn = None
    if not db.exists():
        legacy = me.OLD_MEM_FOLDER / "memoria.db"
        if not custom and legacy.exists():
            line(WARN, f"Aun no existe; se copiara desde {legacy} la primera vez que se use")
        else:
            line(WARN, "No existe todavia (se crea al guardar el primer recuerdo)")
    else:
        try:
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            conn.execute("SELECT 1")
            line(OK, f"Conexion OK, tamano {_human(db.stat().st_size)}")
            check = _scalar(conn, "PRAGMA quick_check")
            line(OK if check == "ok" else BAD, f"Integridad: {check}")
        except Exception as ex:
            conn = None
            line(BAD, f"No se pudo abrir la base de datos: {ex}")

    # ---------------- Recuerdos ----------------
    if conn:
        print(f"\nRecuerdos (namespace '{namespace}')")
        active = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE namespace=?", (namespace,))
        pinned = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE namespace=? AND COALESCE(pinned,0)=1", (namespace,))
        arch = _scalar(conn, "SELECT COUNT(*) FROM archived_facts WHERE namespace=?", (namespace,))
        if active is None or arch is None:
            line(BAD, "Faltan tablas (facts / archived_facts)")
        else:
            line(OK, f"Activos (biblioteca): {active}" + (f" (fijos: {pinned})" if pinned else ""))
            reasons = _q(conn, "SELECT COALESCE(reason,'sin dato'), COUNT(*) FROM archived_facts WHERE namespace=? GROUP BY 1", (namespace,)) or []
            detail = f" ({', '.join(f'{r}: {n}' for r, n in reasons)})" if reasons else ""
            line(OK, f"En el sotano: {arch}{detail}")
            links = _scalar(conn, "SELECT COUNT(*) FROM links WHERE fact_a IN (SELECT id FROM facts WHERE namespace=?)", (namespace,))
            if links is not None:
                line(OK, f"Enlaces entre recuerdos: {links}")
        others = _q(conn, "SELECT namespace, COUNT(*) FROM facts WHERE namespace != ? GROUP BY namespace", (namespace,)) or []
        if others:
            print("  Otros namespaces: " + ", ".join(f"{n} ({c})" for n, c in others))
        scheme = _scalar(conn, "SELECT value FROM meta WHERE key='embed_scheme'")
        current = f"{me.EMBEDDING_MODEL}|{me.QUERY_PREFIX}|{me.DOC_PREFIX}"
        if scheme is None:
            line(WARN, "Los vectores aun no tienen esquema registrado (se recalcularan al arrancar el motor)")
        elif scheme == current:
            line(OK, "Esquema de embeddings al dia")
        else:
            line(WARN, "Los vectores se hicieron con otro modelo/prefijos; se recalcularan solos al arrancar el motor")

    # ---------------- Ollama ----------------
    print("\nOllama (local)")
    names = None
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=3)
        names = {m.get("name", "") for m in r.json().get("models", [])}
        line(OK, f"Responde en localhost:11434 ({len(names)} modelos instalados)")
    except Exception:
        line(BAD, "No responde en localhost:11434 (¿esta encendido?)")
    if names is not None:
        for label, model, ping in (("Embeddings", me.EMBEDDING_MODEL, _ping_embed), ("Decision", me.LLM_MODEL, _ping_llm)):
            if not _installed(model, names):
                line(BAD, f"{label}: el modelo '{model}' no esta instalado (ollama pull {model})")
                continue
            ok, ms, info = ping()
            line(OK if ok else BAD, f"{label}: {model} {info} ({ms:.0f} ms)")
        synth = me.SYNTHESIS_MODEL
        line(OK if _installed(synth, names) else WARN,
             f"Reflexion: {synth} " + ("instalado" if _installed(synth, names) else "no esta instalado (solo afecta a la reflexion)"))

    # ---------------- Sueño ----------------
    print("\nSueno (dream)")
    if conn:
        rows = dict(_q(conn, "SELECT key, value FROM meta WHERE key LIKE 'last_dream:%'") or [])
        mine = rows.get(f"last_dream:{namespace}")
        if mine is None:
            line(WARN, f"Ultimo ciclo en '{namespace}': nunca (el sueno automatico lo hara cuando toque)")
        else:
            ts = float(mine)
            line(OK, f"Ultimo ciclo en '{namespace}': {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')} (hace {(time.time() - ts) / 3600:.1f} h)")
        for key, value in sorted(rows.items()):
            if key != f"last_dream:{namespace}":
                print(f"       otro namespace '{key.split(':', 1)[1]}': {datetime.fromtimestamp(float(value)).strftime('%Y-%m-%d %H:%M:%S')}")
    auto = me.env_var("AUTO_DREAM", "true").lower() == "true" and me.DREAM_EVERY_HOURS > 0
    print(f"       Sueno automatico: {'activado' if auto else 'desactivado'} (cada {me.DREAM_EVERY_HOURS:g} h, tras {me.env_var('DREAM_IDLE_MIN', '10')} min sin uso)")

    # ---------------- Entorno ----------------
    print("\nEntorno")
    line(OK if me.np is not None else WARN, "numpy " + ("instalado" if me.np is not None else "NO instalado: funciona pero mucho mas lento (pip install numpy)"))
    print(f"       Python {sys.version.split()[0]} | piedras por consulta: {me.STONES}")

    if conn:
        conn.close()
    print("\n" + ("Todo en orden." if not bad else f"Se encontraron {bad} problema(s) grave(s)."))
    return 1 if bad else 0
