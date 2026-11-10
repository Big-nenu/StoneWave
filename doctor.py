"""StoneWave Doctor: reporte de salud de la memoria y solucionador de problemas.

Uso:  python mem_cli.py doctor [-n NAMESPACE]

Es de solo lectura (no modifica nada). Si encuentra un problema grave, al final imprime una seccion
"SOLUCIONES SUGERIDAS" con los comandos exactos para repararlo (ollama pull, reiniciar Ollama,
restaurar la base de datos desde un respaldo, etc.). Codigo de salida: 0 todo bien (o solo avisos), 1 hay fallos graves.
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
IS_WIN = sys.platform.startswith("win")


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
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


def _ping_gen(model):
    """Modelo generativo (reflexion y conclusiones): una peticion minima para ver que carga y responde."""
    t = time.perf_counter()
    try:
        r = requests.post(me.OLLAMA_GEN_URL, json={"model": model, "prompt": "ping", "stream": False, "think": False,
                                                   "options": {"num_predict": 1}}, timeout=90)
        ok = r.status_code == 200
        return ok, (time.perf_counter() - t) * 1000, ("responde" if ok else f"HTTP {r.status_code}")
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


# ----------------------------------------------------------------------
# Soluciones: cada funcion devuelve una lista de pasos. Las lineas que empiezan con "$ " son comandos.
# ----------------------------------------------------------------------
def _cp(src, dst):
    return f'$ Copy-Item "{src}" "{dst}" -Force' if IS_WIN else f'$ cp "{src}" "{dst}"'


def _fix_ollama_down():
    steps = ["Ollama no responde en localhost:11434. Arrancalo y comprueba que contesta:"]
    if IS_WIN:
        steps += ["$ ollama serve                       # en otra terminal (o abre la app Ollama desde el menu Inicio)",
                  "$ Get-Process ollama*                # confirma que el proceso existe",
                  "$ netstat -ano | findstr 11434       # confirma que el puerto 11434 esta en uso"]
    elif sys.platform == "darwin":
        steps += ["$ brew services restart ollama      # si lo instalaste con Homebrew (o abre la app Ollama)",
                  "$ ollama serve                       # si lo usas desde la terminal"]
    else:
        steps += ["$ sudo systemctl restart ollama     # si corre como servicio",
                  "$ ollama serve                       # si no usas systemd"]
    steps += ["$ curl" + (".exe" if IS_WIN else "") + " http://localhost:11434/api/tags   # debe devolver la lista de modelos",
              "Despues vuelve a correr: python mem_cli.py doctor"]
    return steps


def _fix_model_missing(model):
    return [f"El modelo '{model}' no esta instalado. Descargalo y comprueba que aparece:",
            f"$ ollama pull {model}",
            "$ ollama list",
            "(Si prefieres otro modelo, cambialo con STONEWAVE_EMBED_MODEL / STONEWAVE_LLM_MODEL en la config del servidor.)"]


def _fix_model_not_responding(model, info):
    if IS_WIN:
        log = r"%LOCALAPPDATA%\Ollama\server.log"
    elif sys.platform == "darwin":
        log = "~/.ollama/logs/server.log"
    else:
        log = "journalctl -u ollama -n 50"
    steps = [f"El modelo '{model}' esta instalado pero no responde bien ({info}). Prueba en este orden:",
             "$ ollama ps                          # ¿se cargo en memoria? (si no hay RAM suficiente, falla al cargar)",
             f"$ ollama stop {model}                # lo descarga; se vuelve a cargar solo en la siguiente peticion",
             f"$ ollama pull {model}                # vuelve a bajarlo por si quedo corrupto o incompleto",
             f"Revisa el log de Ollama: {log}"]
    if "404" in info:
        steps.append("Un HTTP 404 suele indicar que tu version de Ollama (o ese modelo) no ofrece este endpoint: actualiza Ollama a la ultima version.")
    return steps


def _fix_db_open(db, err=None, corrupt=False):
    legacy = me.OLD_MEM_FOLDER / "memoria.db"
    backups = [Path(str(db) + ".bak-embeddings")]
    if not me.env_var("DB"):
        backups.append(legacy)
    found = [b for b in backups if b.exists() and b.resolve() != Path(db).resolve()]
    steps = ["La base de datos esta danada." if corrupt else f"No se pudo abrir la base de datos ({err}). Revisa en este orden:"]
    if not corrupt:
        steps += ["1) Que ningun otro proceso la tenga bloqueada (cierra agentes/servidores que usen StoneWave):",
                  "$ Get-Process python*" if IS_WIN else "$ ps aux | grep -i python",
                  "2) Que tengas permisos sobre el archivo y su carpeta:",
                  f'$ icacls "{db}"' if IS_WIN else f'$ ls -l "{db}"',
                  "3) Si sigue fallando, restaura un respaldo (abajo)."]
    steps.append("Antes de tocar nada, guarda una copia del archivo actual:")
    steps.append(_cp(db, f"{db}.danada"))
    if found:
        steps.append("Restaura el respaldo mas reciente que encontre (se pierde lo guardado despues de ese respaldo):")
        for b in found:
            steps.append(_cp(b, db) + f"      # respaldo: {b}")
    else:
        steps += ["No encontre respaldos automaticos. Intenta recuperar lo que se pueda (requiere la herramienta sqlite3):",
                  f'$ sqlite3 "{db}" ".recover" | sqlite3 "{db}.recuperada"',
                  "Si la recuperada se ve bien, usala como memoria.db (con el servidor cerrado)."]
    steps.append("Despues vuelve a correr: python mem_cli.py doctor")
    return steps


def _fix_missing_tables():
    return ["Faltan tablas en la base de datos (version vieja). El motor las crea solo al arrancar; fuerzalo desde la carpeta del proyecto:",
            f'$ "{sys.executable}" -c "from mem_engine import StoneWaveEngine; StoneWaveEngine().close()"']


def _fix_numpy():
    return ["Sin numpy el motor funciona pero mucho mas lento. Instalalo en el MISMO Python que usa tu agente:",
            f'$ "{sys.executable}" -m pip install numpy']


# ----------------------------------------------------------------------
# Reporte
# ----------------------------------------------------------------------
def run_doctor(namespace="default"):
    """Imprime el reporte. Devuelve 0 si todo esta bien (o solo avisos) y 1 si hay algun problema grave."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    bad = 0
    fixes = []  # (estado, problema, pasos)

    def line(status, text, fix=None):
        nonlocal bad
        if status == BAD:
            bad += 1
        print(f"  {status} {text}")
        if fix and status in (BAD, WARN):
            fixes.append((status, text, fix))

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
            check = conn.execute("PRAGMA quick_check").fetchone()[0]  # en un archivo que no es SQLite, el error salta aqui
            line(OK, f"Conexion OK, tamano {_human(db.stat().st_size)}")
            line(OK if check == "ok" else BAD, f"Integridad: {check}",
                 None if check == "ok" else _fix_db_open(db, corrupt=True))
            if check != "ok":
                conn.close()
                conn = None
        except Exception as ex:
            if conn:
                conn.close()
            conn = None
            corrupt = "not a database" in str(ex).lower() or "malformed" in str(ex).lower()
            line(BAD, f"No se pudo abrir la base de datos: {ex}", _fix_db_open(db, err=ex, corrupt=corrupt))

    # ---------------- Recuerdos ----------------
    if conn:
        print(f"\nRecuerdos (namespace '{namespace}')")
        active = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE namespace=?", (namespace,))
        pinned = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE namespace=? AND COALESCE(pinned,0)=1", (namespace,))
        arch = _scalar(conn, "SELECT COUNT(*) FROM archived_facts WHERE namespace=?", (namespace,))
        if active is None or arch is None:
            line(BAD, "Faltan tablas (facts / archived_facts)", _fix_missing_tables())
        else:
            line(OK, f"Activos (biblioteca): {active}" + (f" (fijos: {pinned})" if pinned else ""))
            reasons = _q(conn, "SELECT COALESCE(reason,'sin dato'), COUNT(*) FROM archived_facts WHERE namespace=? GROUP BY 1", (namespace,)) or []
            detail = f" ({', '.join(f'{r}: {n}' for r, n in reasons)})" if reasons else ""
            line(OK, f"En el sotano: {arch}{detail}")
            links = _scalar(conn, "SELECT COUNT(*) FROM links WHERE fact_a IN (SELECT id FROM facts WHERE namespace=?)", (namespace,))
            if links is not None:
                line(OK, f"Enlaces entre recuerdos: {links}")
            trash = _scalar(conn, "SELECT COUNT(*) FROM trash WHERE namespace=?", (namespace,))
            if trash is not None:
                line(OK, f"En la papelera (olvidados/reemplazados): {trash}")
            concl = _scalar(conn, "SELECT COUNT(*) FROM conclusions WHERE namespace=?", (namespace,))
            if concl is not None:
                pend = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE namespace=? AND COALESCE(reasoned,0)=0", (namespace,))
                line(OK, f"Conclusiones del sueno: {concl} (recuerdos pendientes de razonar: {pend})")
                derived = _scalar(conn, "SELECT COUNT(DISTINCT child_id) FROM conclusion_parents WHERE child_id IN (SELECT id FROM conclusions WHERE namespace=?)", (namespace,))
                hist = _scalar(conn, "SELECT COUNT(*) FROM conclusion_history WHERE namespace=?", (namespace,))
                if derived is not None and hist is not None:
                    line(OK, f"Conclusiones derivadas de otras: {derived} | Historial de conclusiones (caducadas o reemplazadas): {hist}")
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
        line(BAD, "No responde en localhost:11434 (¿esta encendido?)", _fix_ollama_down())
    if names is not None:
        for label, model, ping in (("Embeddings", me.EMBEDDING_MODEL, _ping_embed), ("Decision", me.LLM_MODEL, _ping_llm)):
            if not _installed(model, names):
                line(BAD, f"{label}: el modelo '{model}' no esta instalado (ollama pull {model})", _fix_model_missing(model))
                continue
            ok, ms, info = ping()
            line(OK if ok else BAD, f"{label}: {model} {info} ({ms:.0f} ms)", None if ok else _fix_model_not_responding(model, info))
        # modelos generativos (reflexion y conclusiones): si faltan o no responden es un aviso, el motor sigue funcionando sin ellos
        synth, concl = me.SYNTHESIS_MODEL, me.CONCLUSION_MODEL
        gens = [("Reflexion y conclusiones" if synth == concl else "Reflexion", synth,
                 "la reflexion y las conclusiones" if synth == concl else "la reflexion")]
        if concl != synth:
            gens.append(("Conclusiones", concl, "las conclusiones"))
        for label, model, affects in gens:
            if not _installed(model, names):
                line(WARN, f"{label}: el modelo '{model}' no esta instalado (solo afecta a {affects})", _fix_model_missing(model))
                continue
            ok, ms, info = _ping_gen(model)
            line(OK if ok else WARN, f"{label}: {model} {info} ({ms:.0f} ms)", None if ok else _fix_model_not_responding(model, info))

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
    print(f"       Conclusiones del sueno: {'activadas' if me.CONCLUSIONS_ON else 'apagadas (STONEWAVE_CONCLUSIONS=false)'}"
          + (f" | modelo: {me.CONCLUSION_MODEL}" if me.CONCLUSIONS_ON else ""))
    from notes import notes_dir
    nd = notes_dir()
    print("       Notas de Obsidian: " + (f"activadas en {nd}" + ("" if Path(nd).is_dir() else "  <-- ¡esa carpeta NO existe!") if nd
                                          else "apagadas (define STONEWAVE_NOTES para escribirlas)"))

    # ---------------- Entorno ----------------
    print("\nEntorno")
    has_np = me.np is not None
    line(OK if has_np else WARN, "numpy " + ("instalado" if has_np else "NO instalado: funciona pero mucho mas lento"),
         None if has_np else _fix_numpy())
    print(f"       Python {sys.version.split()[0]} | piedras por consulta: {me.STONES}")

    if conn:
        conn.close()

    # ---------------- Soluciones ----------------
    def show(title, items):
        print("\n" + "=" * 60 + f"\n{title}\n" + "=" * 60)
        for n, (_status, problem, steps) in enumerate(items, 1):
            print(f"\n{n}. {problem}")
            for step in steps:
                print(f"     {step}")

    graves = [f for f in fixes if f[0] == BAD]
    avisos = [f for f in fixes if f[0] == WARN]
    if graves:
        show("SOLUCIONES SUGERIDAS", graves)
    if avisos:
        show("RECOMENDACIONES (no graves)", avisos)

    print("\n" + ("Todo en orden." if not bad else f"Se encontraron {bad} problema(s) grave(s); arriba tienes las soluciones sugeridas."))
    return 1 if bad else 0
