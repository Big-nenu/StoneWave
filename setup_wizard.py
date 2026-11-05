"""StoneWave Setup: deja todo listo en una sola corrida.

Uso:  python mem_cli.py setup [--pull]

Revisa Python y paquetes, revisa Ollama y los modelos, e imprime la configuracion MCP con las rutas ya
puestas (para pegarla en Cline, Claude Desktop, Cursor...) y el comando para instalar la skill.
Solo lectura, salvo con --pull: ahi descarga con 'ollama pull' los modelos que falten.
Codigo de salida: 0 todo listo (o solo avisos), 1 hay algo que arreglar.
"""
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import requests

import mem_engine as me

OK, WARN, BAD = "[OK]", "[!!]", "[XX]"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
BASE = Path(__file__).resolve().parent
IS_WIN = sys.platform.startswith("win")


def _installed(model, names):
    return model in names or f"{model}:latest" in names


def _have(module):
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def build_config(python=None, server=None):
    """Bloque 'mcpServers' con rutas absolutas, listo para pegar en el cliente."""
    return {"mcpServers": {"stonewave": {"command": python or sys.executable,
                                         "args": [str(server or BASE / "mcp_server.py")]}}}


def _skill_commands():
    if IS_WIN:
        return ['New-Item -ItemType Directory -Force "$env:USERPROFILE\\.agents\\skills" | Out-Null',
                'Copy-Item -Recurse skills\\stonewave-memory "$env:USERPROFILE\\.agents\\skills\\"']
    return ["mkdir -p ~/.agents/skills && cp -r skills/stonewave-memory ~/.agents/skills/"]


def _ollama_start_hint():
    if IS_WIN:
        return "ollama serve   (en otra terminal, o abre la app Ollama desde el menu Inicio)"
    if sys.platform == "darwin":
        return "brew services restart ollama   (o abre la app Ollama)"
    return "sudo systemctl restart ollama   (o: ollama serve)"


def run_setup(pull=False, http=None, which=None, runner=None):
    """Imprime el reporte de instalacion. Devuelve 0 si todo esta listo y 1 si hay algo que arreglar."""
    http = http or requests
    which = which or shutil.which
    runner = runner or subprocess.run
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    problems = 0

    def line(status, text):
        nonlocal problems
        if status == BAD:
            problems += 1
        print(f"  {status} {text}")

    print("=== StoneWave Setup ===")

    print("\n1. Python y paquetes")
    py = sys.version.split()[0]
    line(OK if sys.version_info >= (3, 10) else BAD, f"Python {py} ({sys.executable})" + ("" if sys.version_info >= (3, 10) else " - se necesita 3.10 o mas"))
    missing = [name for mod, name in (("mcp", "mcp"), ("requests", "requests")) if not _have(mod)]
    if missing:
        line(BAD, "Faltan paquetes: " + ", ".join(missing))
        print(f'       Instalalos en ESTE Python:  "{sys.executable}" -m pip install -r requirements.txt')
    else:
        line(OK, "mcp y requests instalados")
    if _have("numpy"):
        line(OK, "numpy instalado")
    else:
        line(WARN, f'numpy no instalado (funciona, pero mucho mas lento):  "{sys.executable}" -m pip install numpy')

    print("\n2. Ollama y modelos")
    names = None
    try:
        names = {m.get("name", "") for m in http.get(OLLAMA_TAGS_URL, timeout=3).json().get("models", [])}
        line(OK, f"Ollama responde en localhost:11434 ({len(names)} modelos instalados)")
    except Exception:
        line(BAD, "Ollama no responde en localhost:11434 (esta apagado o no esta instalado: https://ollama.com)")
        print(f"       Arrancalo con:  {_ollama_start_hint()}")
    if names is not None:
        for label, model, required in (("Embeddings", me.EMBEDDING_MODEL, True), ("Decision", me.LLM_MODEL, True),
                                       ("Reflexion", me.SYNTHESIS_MODEL, False)):
            if _installed(model, names):
                line(OK, f"{label}: {model} instalado")
                continue
            if pull and which("ollama"):
                print(f"       Descargando {model} (puede tardar)...")
                done = runner(["ollama", "pull", model], check=False)
                if getattr(done, "returncode", 1) == 0:
                    line(OK, f"{label}: {model} descargado")
                    continue
                line(BAD if required else WARN, f"{label}: no se pudo descargar {model}")
            else:
                line(BAD if required else WARN, f"{label}: falta {model}" + ("" if required else " (solo afecta a la reflexion)"))
            if not (pull and which("ollama")):
                print(f"       $ ollama pull {model}")

    print("\n3. Configuracion MCP (pegala en tu cliente; reinicia el servidor despues)")
    print(json.dumps(build_config(), indent=2, ensure_ascii=False))
    cfg = build_config()["mcpServers"]["stonewave"]
    print(f'\n  Claude Code:  claude mcp add stonewave -- "{cfg["command"]}" "{cfg["args"][0]}"')
    print("  Donde pegarla: Cline (cline_mcp_settings.json), Claude Desktop (claude_desktop_config.json), Cursor (.cursor/mcp.json)")

    print("\n4. Skill (para que el agente use la memoria solo; desde la carpeta del proyecto)")
    for cmd in _skill_commands():
        print(f"  $ {cmd}")

    print("\n5. Para probar")
    print("  En una conversacion nueva pregunta: '¿Que sabes de mi?'  |  Revision de salud:  python mem_cli.py doctor")

    print("\n" + ("Todo listo." if not problems else f"Hay {problems} cosa(s) por arreglar; revisa los [XX] de arriba."))
    return 1 if problems else 0
