import contextlib
import os
import sys
import webbrowser
from pathlib import Path
from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import mem_engine
from mem_engine import MemHolograficEngine
from graph_engine import GraphEngine

# Configuración de Rutas Profesionales
DB_PATH = os.getenv("MEMHOLOGRAFIC_DB", str(Path.home() / ".memholografic" / "memoria.db"))
NS = os.getenv("MEMHOLOGRAFIC_NAMESPACE", "default")
GRAPH_ENABLED = os.getenv("MEMHOLOGRAFIC_GRAPH_ENABLED", "false").lower() == "true"

INSTRUCTIONS = (
    "Memoria persistente del usuario entre conversaciones. "
    "AL INICIO de cada conversación llama list_memories antes de responder. "
    "Cuando el usuario diga algo duradero sobre sí mismo, sus proyectos o sus decisiones, llama inject_memory. "
    "Antes de responder sobre el usuario o su historial, llama query_memory. "
    "Si el usuario quiere ver el grafo de relaciones de su memoria, llama generate_graph (requiere MEMHOLOGRAFIC_GRAPH_ENABLED=true)."
)

mcp = FastMCP("MemHolografic", instructions=INSTRUCTIONS)
engine = MemHolograficEngine(db_path=DB_PATH)

def _quiet():
    return contextlib.redirect_stdout(sys.stderr)

def _all_memories(limit: int = 30) -> str:
    with _quiet():
        facts = engine.get_all_facts(namespace=NS)
    if not facts:
        return "La memoria está vacía todavía."
    shown = facts[:limit]
    text = "\n".join(f"- {f}" for f in shown)
    extra = f"\n(Se muestran los {len(shown)} con más energía de {len(facts)})" if len(facts) > len(shown) else ""
    return f"Memoria ({len(facts)} recuerdos):\n{text}{extra}"

@mcp.tool()
def list_memories(limit: int = 30) -> str:
    """LLAMA ESTO AL INICIO de CADA CONVERSACIÓN. Devuelve lo que ya se sabe del usuario."""
    return _all_memories(limit)

@mcp.tool()
def query_memory(text: str) -> str:
    """Busca recuerdos relacionados con un tema."""
    with _quiet():
        res = engine.query(text, namespace=NS)
    if isinstance(res, str):
        if res.startswith("Error"):
            return f"No se pudo buscar ({res})."
        return "Sin coincidencias exactas. Esto es lo guardado:\n" + _all_memories()
    if not res:
        return "Sin coincidencias exactas. Esto es lo guardado:\n" + _all_memories()
    lines = "\n".join(f"Sim: {sim:.2f} | {content}" for sim, fid, energy, content in res)
    return f"Recuerdos encontrados:\n{lines}"

@mcp.tool()
def inject_memory(content: str, energy: float = 0.5) -> str:
    """Guarda un dato duradero: preferencia, decisión, dato personal o técnico."""
    with _quiet():
        result = engine.inject(content, energy=energy, namespace=NS)
    return str(result) if result is not None else "No se pudo guardar."

@mcp.tool()
def force_dream() -> str:
    """Mantenimiento: consolida la memoria y manda lo poco relevante al sótano."""
    with _quiet():
        result = engine.dream(namespace=NS)
    return str(result) if result is not None else "El proceso de sueño no generó resultados."

@mcp.tool()
def trigger_reflection() -> str:
    """Genera una intuición nueva conectando los recuerdos más fuertes."""
    with _quiet():
        result = engine.reflect(namespace=NS)
    return str(result) if result is not None else "No se encontró una conexión interesante esta vez."

@mcp.tool()
def clear_memory(confirm: bool = False) -> str:
    """BORRA TODA la memoria. Irreversible."""
    if not confirm:
        return "Cancelado: falta confirm=true."
    with _quiet():
        engine.clear_db(namespace=NS)
    return "Memoria borrada."

@mcp.tool()
def generate_graph(open_browser: bool = True) -> str:
    """Genera un grafo visual de las relaciones entre entidades de la memoria."""
    if not GRAPH_ENABLED:
        return "El generador de grafo está desactivado."
    
    graph_engine = GraphEngine(DB_PATH)
    with _quiet():
        user_name = os.getlogin()
        resultado = graph_engine.generate_graph_html(namespace=NS, user_name=user_name)
        
    output_path = resultado.get("output_path", "memoria_grafo.html")
    mensaje = f"Grafo generado: {resultado['entities']} entidades, {resultado['relationships']} relaciones. Archivo: {output_path}"
    if open_browser:
        try:
            webbrowser.open("file://" + os.path.abspath(output_path))
            mensaje += " (abierto en el navegador)."
        except Exception as e:
            mensaje += f" (no se pudo abrir el navegador: {e})."
    return mensaje

if __name__ == "__main__":
    mcp.run()
