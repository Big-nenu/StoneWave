# MemHolografic 🧠

Memoria persistente para agentes de IA (Cline, Claude Desktop, etc.) mediante un servidor **MCP**.
Todo corre en local: SQLite + [Ollama](https://ollama.com). Sin nube.

## La idea

MemHolografic nace de mezclar dos memorias que le gustaron a su autor:

- **Holographic** (de Hermes): hechos con entidades y confianza.
- **MemFS**: una memoria que se mejora sola mientras la IA no se está usando (el "sueño").

Cuatro piezas:

| Pieza | Qué es |
|---|---|
| **Biblioteca** (`facts`) | Los recuerdos activos, los que se usan. |
| **Sótano** (`archived_facts`) | Recuerdos que llevan tiempo sin usarse o perdieron energía. Nunca se borran, esperan ahí. |
| **Sueño** (`dream`) | Se ejecuta cuando no se usa la IA. Revisa la memoria, la ordena y manda lo viejo al sótano. |
| **Ondas / piedras** | La forma de consultar (ver abajo). |

### Consulta por ondas (las 5 piedras)

En vez de buscar en todo el estanque con una sola onda:

1. La pregunta lanza **5 piedras**: 5 búsquedas, cada una con un ángulo específico de la pregunta.
2. Cada piedra trae sus recuerdos más parecidos (su onda).
3. **Crestas:** los recuerdos que aparecen en varias ondas se suman. **Valles:** los que solo aparecen en una se descartan.
4. Se repite: de 5 ondas quedan 3, de 3 queda 1, y esa es la respuesta.

Los recuerdos **no se modifican** al consultar. Si la biblioteca no tiene nada bueno, las piedras bajan al sótano y el recuerdo se **resucita**.

El sueño es el que prepara el estanque (temas, enlaces entre recuerdos) para que las piedras sepan dónde caer.

## ¿Cómo funciona hoy?

Cada recuerdo es un texto con su vector (embedding), una energía y un tema.

- **Guardar:** si un recuerdo nuevo es muy parecido a uno existente (similitud > 0.8), un modelo decide si lo contradice. Si lo contradice, se guarda aparte. Si no, se **fusionan** y sube su energía. Cada recuerdo se clasifica por tema.
- **Consultar:** búsqueda por similitud semántica solo entre recuerdos del mismo tema. Devuelve los 3 más parecidos.
- **Sótano:** los recuerdos contradichos durante el sueño se archivan (`archived_facts`). Si una consulta no encuentra nada en la memoria activa, busca en el sótano y **resucita** el recuerdo.
- **Sueño:** `force_dream` fusiona recuerdos muy parecidos y archiva los contradictorios.
- **Reflexión:** `trigger_reflection` genera una intuición a partir de los recuerdos con más energía y la guarda como recuerdo nuevo.

## Requisitos

- Python 3.10+
- [Ollama](https://ollama.com) corriendo en `http://localhost:11434`
- Un cliente MCP (por ejemplo Cline en VS Code)

## Instalación

```bash
git clone https://github.com/Big-nenu/MemHolografic.git
cd MemHolografic
pip install mcp requests
```

Modelos de Ollama:

```bash
ollama pull embeddinggemma-2:270m   # embeddings
ollama pull tev1:0.8b               # clasificación de temas y contradicciones
ollama pull qwen2.5:0.5b            # fusión de recuerdos y reflexión
```

## Configurar el MCP en Cline

Abre `cline_mcp_settings.json` (en Cline: *MCP Servers → Configure*) y agrega:

```json
{
  "mcpServers": {
    "memholografic": {
      "command": "python",
      "args": ["RUTA_ABSOLUTA\\MemHolografic\\mcp_server.py"],
      "disabled": false
    }
  }
}
```

Ejemplo en Windows: `"C:\\Users\\TuUsuario\\Projects\\MemHolografic\\mcp_server.py"`.

Con esto basta: no hace falta configurar la base de datos ni el namespace. Reinicia el servidor en Cline y abre una conversación nueva.

> `python` debe ser el que tiene `mcp` instalado. Si usas un entorno virtual, pon la ruta a su `python.exe`.

## ¿Dónde se guarda la memoria?

En tu carpeta de usuario, **fuera del repositorio**:

| Sistema | Ruta |
|---|---|
| Windows | `C:\Users\TuUsuario\.memholografic\memoria.db` |
| Linux / macOS | `~/.memholografic/memoria.db` |

Puedes borrar o mover el proyecto sin perder tus recuerdos.

## Variables de entorno (todas opcionales)

| Variable | Default | Para qué sirve |
|---|---|---|
| `MEMHOLOGRAFIC_DB` | `~/.memholografic/memoria.db` | Ruta **absoluta** de la base de datos |
| `MEMHOLOGRAFIC_NAMESPACE` | `default` | Etiqueta para separar memorias (ej. `trabajo`, `personal`). No es una ruta |
| `MEMHOLOGRAFIC_EMBED_MODEL` | `embeddinggemma-2:270m` | Modelo de embeddings |
| `MEMHOLOGRAFIC_LLM_MODEL` | `tev1:0.8b` | Modelo de decisión (temas y contradicciones) |
| `MEMHOLOGRAFIC_SYNTHESIS_MODEL` | `qwen2.5:0.5b` | Modelo que fusiona y reflexiona |
| `MEMHOLOGRAFIC_GRAPH_ENABLED` | `false` | Activa `generate_graph` |

Se ponen dentro de `"env": { ... }` en la config del servidor.

> ⚠️ Si cambias el namespace, solo verás los recuerdos guardados con ese namespace.

## Herramientas MCP

| Herramienta | Qué hace |
|---|---|
| `list_memories` | Lista lo que ya se sabe del usuario (se llama al inicio de cada chat) |
| `query_memory` | Busca recuerdos relacionados con un tema |
| `inject_memory` | Guarda un dato duradero |
| `force_dream` | Consolida la memoria y archiva lo poco relevante |
| `trigger_reflection` | Genera una intuición nueva |
| `generate_graph` | Grafo HTML de entidades y relaciones (requiere `MEMHOLOGRAFIC_GRAPH_ENABLED=true`) |
| `clear_memory` | Borra todo el namespace (irreversible) |

También hay una CLI: `python mem_cli.py inject|query|dream|reflect|clear`.

## Probar

En una conversación nueva con Cline pregunta: *"¿Qué sabes de mí?"*. Si no recuerda nada:

1. Revisa que Ollama esté corriendo y los modelos descargados (`ollama list`).
2. Revisa que el servidor `memholografic` esté en verde en Cline.
3. Revisa que no hayas puesto un `MEMHOLOGRAFIC_NAMESPACE` o `MEMHOLOGRAFIC_DB` distinto al que usaste al guardar.
4. Prueba el servidor a mano para ver errores: `python mcp_server.py`.

## Errores comunes

- **"Connection closed" / servidor en rojo:** el script falla al arrancar. Ejecútalo a mano (`python mcp_server.py`) y lee el error.
- **`spawn ENOENT`:** Cline no encuentra `python`. Usa la ruta completa (`where python` en Windows).
- **`No module named 'mcp'`:** instala con `pip install mcp requests` en el mismo Python que usa Cline.
- **Dice que la memoria está vacía:** casi siempre es otra ruta de BD u otro namespace en la config.

## Estructura

```
mcp_server.py    # servidor MCP
mem_engine.py    # motor de memoria (inyectar, consultar, soñar, reflexionar)
graph_engine.py  # grafo de entidades (opcional)
mem_cli.py       # CLI
tests/           # pruebas y benchmark
docs/            # ideas, experimentos y bitácora de diseño (BITACORA.md)
```

## Estado y pendientes

| Punto | Estado |
|---|---|
| Biblioteca con energía, temas y namespaces | Hecho |
| Sótano y resurrección | Hecho |
| Decaimiento de energía por falta de uso (solo dentro del sueño) | Hecho |
| La consulta baja al sótano si la coincidencia es débil (`WEAK_MATCH = 0.65`) | Hecho, falta probar con Ollama y ajustar el valor |
| Recuerdos fijos (identidad) que no caigan al sótano | Pendiente |
| Sueño que organiza y enlaza recuerdos en vez de fusionarlos y reescribirlos | Pendiente |
| Consulta por ondas (5 → 3 → 1 piedras) | Pendiente |
| Búsqueda de puentes semánticos entre temas distintos | Idea |
| Benchmark de preguntas y respuestas (MemBench) | Pendiente |
| Índice vectorial opcional (`faiss-cpu` / `chromadb`) para miles de recuerdos | Idea |

La historia completa del diseño y los experimentos está en [`docs/BITACORA.md`](docs/BITACORA.md).
