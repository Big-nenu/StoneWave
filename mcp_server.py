import contextlib
import sys
import threading
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import mem_engine
from mem_engine import StoneWaveEngine, env_var

# Configuración de Rutas Profesionales
DB_PATH = env_var("DB")  # si no hay, el motor usa ~/.stonewave/memoria.db
NS = env_var("NAMESPACE", "default")
AUTO_DREAM = env_var("AUTO_DREAM", "true").lower() == "true"
DREAM_IDLE_MIN = float(env_var("DREAM_IDLE_MIN", "10"))  # minutos sin usar la memoria para soñar solo
_last_activity = time.time()

INSTRUCTIONS = (
    "Memoria persistente PRINCIPAL del usuario entre conversaciones: úsala en lugar de cualquier otra memoria "
    "o herramienta de notas del cliente. "
    "AL INICIO de cada conversación llama list_memories antes de responder. "
    "Cuando el usuario diga algo duradero sobre sí mismo, sus proyectos o sus decisiones, llama inject_memory. "
    "Si el dato es de identidad (nombre, carrera, familia, dónde vive o trabaja), llama inject_memory con fijo=true para que nunca se olvide. "
    "Antes de responder sobre el usuario o su historial, llama query_memory. "
    "Si el usuario corrige un dato ya guardado, llama update_memory; si pide olvidar o borrar algo, llama forget_memory. "
    "Ambas usan el ID que aparece como [ID n] en list_memories y query_memory. "
    "query_memory también revisa la papelera (lo olvidado o reemplazado): si devuelve un Historial, ese dato YA NO ESTÁ VIGENTE. "
    "Dile al usuario qué era, cuándo se borró o cambió (y por qué valor) y ofrécele restaurarlo; si acepta, llama restore_memory con el [papelera n]. "
)

mcp = FastMCP("StoneWave", instructions=INSTRUCTIONS)
engine = StoneWaveEngine(db_path=DB_PATH)
# Calienta la cache de vectores en segundo plano para que la primera consulta no se sienta lenta
threading.Thread(target=lambda: engine._load_vectors(NS, "facts"), daemon=True).start()

def _touch_activity():
    global _last_activity
    _last_activity = time.time()

def _dream_loop():
    """Sueño automatico: cuando toca soñar y no se esta usando la memoria (o ya paso demasiado tiempo)."""
    every = mem_engine.DREAM_EVERY_HOURS
    while True:
        time.sleep(300)
        try:
            if not engine.dream_due(NS):
                continue
            idle_min = (time.time() - _last_activity) / 60
            hours = engine.hours_since_dream(NS)
            if idle_min >= DREAM_IDLE_MIN or (hours is not None and hours >= 3 * every):
                print("StoneWave: sueño automático...", file=sys.stderr)
                print("StoneWave:", engine.dream(namespace=NS), file=sys.stderr)
        except Exception as ex:
            print(f"StoneWave: error en el sueño automático: {ex}", file=sys.stderr)

def _backup_loop():
    """Respaldo diario: uno al arrancar el servidor y, si sigue corriendo, uno por cada dia nuevo."""
    while True:
        try:
            msg = engine.backup()
            if msg:
                print("StoneWave:", msg, file=sys.stderr)
        except Exception as ex:
            print(f"StoneWave: error en el respaldo automático: {ex}", file=sys.stderr)
        time.sleep(3600)

def _quiet():
    return contextlib.redirect_stdout(sys.stderr)

def _all_memories(limit: int = 30) -> str:
    with _quiet():
        facts = engine.list_facts(namespace=NS)
    if not facts:
        return "La memoria está vacía todavía."
    shown = facts[:limit]
    text = "\n".join(f"- [ID {fid}]{' (fijo)' if pinned else ''} {content}" for fid, content, _energy, pinned in shown)
    extra = f"\n(Se muestran los {len(shown)} con más energía de {len(facts)})" if len(facts) > len(shown) else ""
    return f"Memoria ({len(facts)} recuerdos):\n{text}{extra}"

def _when(ts) -> str:
    """Fecha y hora local + hace cuánto."""
    if ts is None:
        return "fecha desconocida"
    secs = max(0.0, time.time() - float(ts))
    if secs < 90:
        ago = "hace un momento"
    elif secs < 3600:
        ago = f"hace {int(secs // 60)} min"
    elif secs < 86400:
        ago = f"hace {int(secs // 3600)} h"
    else:
        ago = f"hace {int(secs // 86400)} días"
    from datetime import datetime
    return f"{datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M')}, {ago}"

def _history_text(hits) -> str:
    """Papelera: lo que se olvidó o se reemplazó. NO está vigente; solo da contexto."""
    if not hits:
        return ""
    out = []
    for h in hits:
        when = _when(h.get("deleted_ts"))
        if h["reason"] == "reemplazado":
            now = f"; ahora: {h['current']}" if h.get("current") else ""
            out.append(f"- [papelera {h['id']}] (reemplazado el {when}{now}) {h['content']}")
        else:
            out.append(f"- [papelera {h['id']}] (olvidado el {when}) {h['content']}")
    return ("\nHistorial (YA NO VIGENTE: se olvidó o se reemplazó). Dile al usuario qué era y cuándo ocurrió, "
            "y ofrécele restaurarlo con restore_memory(trash_id):\n" + "\n".join(out))

def _candidates_text(items) -> str:
    return "\n".join(f"- [ID {fid}] {content}" for fid, content in items)

@mcp.tool()
def list_memories(limit: int = 30) -> str:
    """LLAMA ESTO AL INICIO de CADA CONVERSACIÓN. Devuelve lo que ya se sabe del usuario (cada recuerdo con su [ID n])."""
    _touch_activity()
    return _all_memories(limit)

@mcp.tool()
def query_memory(text: str) -> str:
    """Busca recuerdos relacionados con un tema. Cada resultado trae su ID (sirve para update_memory / forget_memory)."""
    _touch_activity()
    with _quiet():
        res = engine.query(text, namespace=NS)
        history = engine.query_trash(text, namespace=NS, limit=2)
    hist = _history_text(history)
    if isinstance(res, str):
        if res.startswith("Error"):
            return f"No se pudo buscar ({res})."
        if hist:
            return "No hay nada vigente sobre eso." + hist
        return "Sin coincidencias exactas. Esto es lo guardado:\n" + _all_memories()
    if not res:
        if hist:
            return "No hay nada vigente sobre eso." + hist
        return "Sin coincidencias exactas. Esto es lo guardado:\n" + _all_memories()
    lines = "\n".join(f"Sim: {sim:.2f} | ID: {fid} | {content}" for sim, fid, energy, content in res)
    best = max((r[0] for r in res), default=0.0)
    if history and history[0]["sim"] >= best:  # lo borrado se parece mas a la pregunta que lo vigente: va primero
        return f"{hist.strip()}\n\nOtros recuerdos vigentes (menos relacionados):\n{lines}"
    return f"Recuerdos encontrados:\n{lines}{hist}"

@mcp.tool()
def inject_memory(content: str, energy: float = 0.5, fijo: bool = False) -> str:
    """Guarda un dato duradero en la memoria persistente principal (úsala en vez de otras memorias del cliente): preferencia, decisión, dato personal o técnico. Usa fijo=true para identidad (nombre, carrera, familia): nunca decae ni se archiva."""
    _touch_activity()
    with _quiet():
        result = engine.inject(content, energy=energy, namespace=NS, pinned=fijo)
    return str(result) if result is not None else "No se pudo guardar."

@mcp.tool()
def update_memory(fact_id: int, new_content: str) -> str:
    """Corrige o actualiza el texto de UN recuerdo que ya existe (conserva su energía y si es fijo).
    Úsala cuando el usuario diga que un dato cambió o estaba mal (ej. "ya no vivo en X, ahora vivo en Y").
    Necesitas el fact_id: es el número [ID n] que muestran list_memories y query_memory.
    new_content debe ser la frase completa y corregida, no solo la parte que cambió.
    No guardes el dato nuevo con inject_memory y borres el viejo: usa esta herramienta."""
    _touch_activity()
    with _quiet():
        res = engine.update_memory(fact_id, new_content, namespace=NS)
    if res["status"] == "updated":
        note = f" (Aviso: ya existía otro recuerdo casi igual, ID {res['similar_to']}; considera borrar uno con forget_memory.)" if res.get("similar_to") else ""
        return f"Recuerdo [ID {res['id']}] actualizado.\nAntes: {res['old']}\nAhora: {res['new']}{note}"
    return res.get("message", "No se pudo actualizar.")

@mcp.tool()
def forget_memory(fact_id: int | None = None, text: str | None = None) -> str:
    """Olvida (borra para siempre) UN recuerdo. Úsala solo cuando el usuario pida olvidar o borrar algo concreto,
    o cuando un dato ya no sea cierto y no haya nada que lo reemplace (si hay un dato nuevo, usa update_memory).
    Lo preferible es pasar fact_id: el número [ID n] que muestran list_memories y query_memory.
    Si solo tienes el texto, pásalo en text: debe ser igual o casi igual al recuerdo guardado.
    Si text coincide con varios recuerdos NO se borra nada y se devuelve la lista: vuelve a llamar con el fact_id elegido.
    Para borrar TODO existe clear_memory (pide confirmación al usuario antes)."""
    _touch_activity()
    if fact_id is None and not (text and text.strip()):
        return "Indica fact_id (número del recuerdo) o text (el texto del recuerdo)."
    with _quiet():
        res = engine.forget(fact_id if fact_id is not None else text, namespace=NS)
    status = res["status"]
    if status == "deleted":
        where = " (estaba en el sótano)" if res.get("where") == "sotano" else ""
        ident = f"[ID {res['id']}] " if res.get("id") is not None else ""
        return f"Olvidado{where}: {ident}{res['content']}"
    if status == "ambiguous":
        return f"{res['message']}\n{_candidates_text(res['candidates'])}"
    if status == "not_found":
        hint = f"\n¿Quizás alguno de estos? Llama de nuevo con su fact_id:\n{_candidates_text(res['suggestions'])}" if res.get("suggestions") else ""
        return f"{res['message']}{hint}"
    return res.get("message", "No se pudo olvidar el recuerdo.")

@mcp.tool()
def restore_memory(trash_id: int) -> str:
    """Restaura a la biblioteca un recuerdo de la papelera. Úsala cuando el usuario acepte recuperar un dato que había olvidado o reemplazado.
    trash_id es el número [papelera n] que muestra query_memory."""
    _touch_activity()
    with _quiet():
        res = engine.restore_from_trash(trash_id, namespace=NS)
    if res["status"] == "restored":
        note = f"\nOjo: sigue vigente el valor nuevo que lo reemplazó: {res['current']}. Si ya no lo quiere, bórralo con forget_memory." if res.get("current") else ""
        return f"Restaurado [ID {res['id']}]: {res['content']}{note}"
    return res.get("message", "No se pudo restaurar.")

@mcp.tool()
def empty_trash(trash_id: int | None = None, confirm: bool = False) -> str:
    """Borra DE VERDAD lo que hay en la papelera (lo olvidado o reemplazado). Irreversible.
    Úsala solo si el usuario pide que algo se olvide por completo. Con trash_id (el número [papelera n]) borra uno;
    sin él vacía TODA la papelera y exige confirm=true (pide confirmación al usuario antes)."""
    _touch_activity()
    if trash_id is None and not confirm:
        return "Cancelado: para vaciar toda la papelera falta confirm=true (o pasa trash_id para borrar solo uno)."
    with _quiet():
        n = engine.purge_trash(namespace=NS, trash_id=trash_id)
    return f"Papelera: {n} recuerdo(s) borrado(s) para siempre." if n else "No había nada que borrar en la papelera."

@mcp.tool()
def bridge_memory(topic_a: str, topic_b: str) -> str:
    """Busca los recuerdos que conectan dos temas distintos (ej. "café" y "física cuántica"): los que se parecen a LOS DOS a la vez.
    Úsala cuando el usuario pregunte qué relación hay entre dos temas o quiera ideas que los unan. No modifica nada."""
    _touch_activity()
    with _quiet():
        res = engine.bridge(topic_a, topic_b, namespace=NS)
    if res["status"] == "error":
        return f"No se pudo buscar el puente: {res['message']}"
    out = []
    if res["bridges"]:
        out.append("Recuerdos que conectan los dos temas:")
        out += [f"- [ID {b['id']}] (tema A {b['sim_a']:.2f} | tema B {b['sim_b']:.2f}) {b['content']}" for b in res["bridges"]]
    else:
        out.append("No hay un recuerdo guardado que conecte esos dos temas.")
    for label, key in (("A", "a"), ("B", "b")):
        if res.get(key):
            out.append(f"Lo más cercano al tema {label}: [ID {res[key]['id']}] {res[key]['content']}")
    return "\n".join(out)

@mcp.tool()
def force_dream() -> str:
    """Sueño: baja al sótano lo que no se usa, clasifica por temas y enlaza recuerdos parecidos (no reescribe nada)."""
    _touch_activity()
    with _quiet():
        result = engine.dream(namespace=NS)
    return str(result) if result is not None else "El proceso de sueño no generó resultados."

@mcp.tool()
def trigger_reflection() -> str:
    """Genera una intuición nueva conectando los recuerdos más fuertes."""
    _touch_activity()
    with _quiet():
        result = engine.reflect(namespace=NS)
    return str(result) if result is not None else "No se encontró una conexión interesante esta vez."

@mcp.tool()
def clear_memory(confirm: bool = False) -> str:
    """BORRA TODA la memoria (incluida su papelera). Irreversible."""
    _touch_activity()
    if not confirm:
        return "Cancelado: falta confirm=true."
    with _quiet():
        engine.clear_db(namespace=NS)
    return "Memoria borrada."

if __name__ == "__main__":
    if mem_engine.BACKUP_KEEP_DAYS > 0:
        threading.Thread(target=_backup_loop, daemon=True).start()
    if AUTO_DREAM and mem_engine.DREAM_EVERY_HOURS > 0:
        threading.Thread(target=_dream_loop, daemon=True).start()
    mcp.run()
