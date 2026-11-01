# StoneWave 🪨

**Local memory for AI agents**

*Five stones. Multiple waves. One answer.*  
*Cinco piedras. Varias ondas. Una respuesta.*

Memoria persistente para cualquier agente de IA que soporte **MCP** (Cline, Claude Desktop, Claude Code, Cursor...).
Todo corre en local: SQLite + [Ollama](https://ollama.com). Sin nube.

## La idea

StoneWave nace de mezclar dos memorias que le gustaron a su autor:

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

Cada recuerdo es un texto con su vector (embedding), una energía y, opcionalmente, un tema.

- **Guardar (`inject_memory`):** es rápido y no usa modelos de lenguaje. Si ya existe un recuerdo casi idéntico (similitud ≥ 0.90) solo le sube la energía. Si no, guarda uno nuevo. **Nunca reescribe ni fusiona recuerdos.** Con `fijo=true` el recuerdo es de identidad: no decae y nunca se archiva.
- **Consultar (`query_memory`):** lanza las **5 piedras** (la pregunta y hasta 4 ángulos sacados de ella) en una sola llamada de embeddings. Cada piedra trae su onda de recuerdos parecidos. Primera ronda: las crestas se suman y los valles se descartan. Los recuerdos enlazados por el sueño se agregan a los candidatos. Segunda ronda: los 3 mejores se vuelven las nuevas piedras y gana quien más se parece a ellos. Devuelve hasta 3 resultados.
- **Sótano:** si la mejor coincidencia de la biblioteca es débil, las piedras también bajan al sótano y el Bibliotecario **resucita** el recuerdo.
- **Sueño (`force_dream`):** (1) baja al sótano lo que lleva tiempo sin usarse, (2) clasifica por tema los recuerdos nuevos, (3) enlaza los recuerdos parecidos sin modificarlos y (4) si dos recuerdos se contradicen, el más viejo baja al sótano (el modelo tiene que decirlo en los dos órdenes para que cuente). Lo que baja por contradicción nunca se resucita solo.
- **Reflexión:** `trigger_reflection` genera una intuición a partir de los recuerdos con más energía y la guarda como recuerdo nuevo.

### ¿Por qué es rápida?

- Los vectores viven en una matriz de numpy en memoria; una consulta es una multiplicación de matrices, no un ciclo en Python.
- Las 5 piedras piden sus embeddings en **una sola llamada** a Ollama.
- Guardar y consultar no llaman a ningún modelo de lenguaje. Los modelos solo se usan en el sueño.
- La cache se actualiza sola si otro proceso (por ejemplo la CLI) cambia la base de datos.
- Medido con 5,000 recuerdos de 768 dimensiones (sin contar Ollama): consulta ≈ 3 ms, guardar ≈ 20 ms. Prueba tú mismo: `python tests/bench_speed.py`.
- Sin numpy funciona igual, pero mucho más lento.

### Calidad de la búsqueda (medida con Ollama real)

La primera prueba con Ollama real salió mal (3 de 11 preguntas acertadas) y no era culpa de las ideas sino de dos cosas del modelo de embeddings:

1. **Prefijos:** `embeddinggemma` espera un prefijo distinto para preguntas y para documentos. Ya se agregan solos (también para `nomic-embed-text`).
2. **Centrado:** todos los vectores comparten una parte "genérica" que hace que todo se parezca a todo (similitudes de 0.65 a 0.85 para cualquier par). Se le resta la media de la memoria y los vectores se separan mucho mejor.

| Prueba (12 recuerdos, 11 preguntas) | Antes | Ahora |
|---|---|---|
| Acierto en el primer resultado | 3/11 | 10/11 (top-3: 11/11) |
| Similitud de lo correcto vs. lo incorrecto | 0.79 vs 0.70 | 0.43 vs 0.19 |

Con esos datos se calibraron los umbrales (duplicados: 0.94–0.97, contradicciones: 0.44–0.76, complementarios: 0.39–0.56, sin relación: negativo).

Sobre las piedras, con esta muestra chica: en preguntas de un solo hecho una piedra acierta igual o un poco mejor (11/11 vs 10/11); en preguntas que necesitan varios recuerdos las 5 piedras cubren más (7/8 vs 6/8). Los ángulos inventados por un LLM salieron peores y más lentos que las palabras clave, por eso están apagados.

**Migración automática:** si cambias de modelo o de prefijos, al arrancar el motor recalcula los vectores de todos los recuerdos y guarda un respaldo en `memoria.db.bak-embeddings`. Los textos no se tocan.

## Requisitos

- Python 3.10+
- [Ollama](https://ollama.com) corriendo en `http://localhost:11434`
- Un agente o editor con soporte MCP (Cline, Claude Desktop, Claude Code, Cursor...)

## Instalación

```bash
git clone https://github.com/Big-nenu/StoneWave.git
cd StoneWave
pip install mcp requests numpy
```

Modelos de Ollama:

```bash
ollama pull embeddinggemma-2:270m   # embeddings
ollama pull tev1:0.8b               # clasificación de temas y contradicciones
ollama pull qwen2.5:0.5b            # fusión de recuerdos y reflexión
```

## Conectar a tu agente

StoneWave es un servidor **MCP**, así que funciona con cualquier agente o editor que soporte MCP, no solo con uno. La configuración base es la misma en casi todos:

```json
{
  "mcpServers": {
    "stonewave": {
      "command": "python",
      "args": ["RUTA_ABSOLUTA/mcp_server.py"]
    }
  }
}
```

Ejemplo de ruta en Windows: `"C:\\Users\\TuUsuario\\Projects\\StoneWave\\mcp_server.py"`. Con esto basta: no hace falta configurar la base de datos ni el namespace. Reinicia el servidor en tu agente y abre una conversación nueva.

> `python` debe ser el que tiene `mcp`, `requests` y `numpy` instalados. Si usas un entorno virtual, pon la ruta a su `python.exe`.

Dónde se pega esa configuración según el cliente (rutas habituales; pueden cambiar según la versión, revisa la documentación de tu cliente):

| Cliente | Dónde |
|---|---|
| Cline | `cline_mcp_settings.json` (MCP Servers → Configure) |
| Claude Desktop | `claude_desktop_config.json` (Settings → Developer → Edit Config) |
| Claude Code | `claude mcp add stonewave -- python RUTA/mcp_server.py` |
| Cursor | `.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| VS Code (Copilot) | `.vscode/mcp.json` (usa `"servers"` en vez de `"mcpServers"`) |

### Que el agente use la memoria sin que se lo pidas

El servidor le dice al agente cuándo usar cada herramienta, pero no todos los clientes hacen caso de esas instrucciones. Si el tuyo no lo hace, pega esto en su archivo de reglas (`CLAUDE.md`, `AGENTS.md`, `.clinerules`, reglas de Cursor...):

> Al inicio de cada conversación llama `list_memories`. Cuando el usuario diga algo duradero sobre sí mismo, sus proyectos o sus decisiones, llama `inject_memory` (con `fijo=true` si es de identidad: nombre, carrera, familia). Antes de responder sobre el usuario o su historial, llama `query_memory`.

### Si tu agente no usa MCP

- **CLI:** `python mem_cli.py inject|query|dream|reflect|clear`. Sirve para cualquier agente que pueda ejecutar comandos.
- **Python:** `from mem_engine import StoneWaveEngine` para usarlo dentro de tu propio agente.
- **API HTTP local:** pendiente (ver la tabla de estado).

Varios agentes pueden usar la misma memoria a la vez: la cache de vectores se actualiza sola cuando otro proceso cambia la base de datos.

## ¿Dónde se guarda la memoria?

En tu carpeta de usuario, **fuera del repositorio**:

| Sistema | Ruta |
|---|---|
| Windows | `C:\Users\TuUsuario\.stonewave\memoria.db` |
| Linux / macOS | `~/.stonewave/memoria.db` |

Puedes borrar o mover el proyecto sin perder tus recuerdos.

## Variables de entorno (todas opcionales)

| Variable | Default | Para qué sirve |
|---|---|---|
| `STONEWAVE_DB` | `~/.stonewave/memoria.db` | Ruta **absoluta** de la base de datos |
| `STONEWAVE_NAMESPACE` | `default` | Etiqueta para separar memorias (ej. `trabajo`, `personal`). No es una ruta |
| `STONEWAVE_EMBED_MODEL` | `embeddinggemma-2:270m` | Modelo de embeddings |
| `STONEWAVE_LLM_MODEL` | `tev1:0.8b` | Modelo de decisión (temas y contradicciones) |
| `STONEWAVE_SYNTHESIS_MODEL` | `qwen2.5:0.5b` | Modelo que reflexiona (y que inventa los ángulos si activas `STONES_LLM`) |
| `STONEWAVE_STONES` | `5` | Piedras por consulta |
| `STONEWAVE_STONES_LLM` | `false` | Si es `true`, un modelo inventa los ángulos de las piedras (en las pruebas salió peor y más lento) |
| `STONEWAVE_QUERY_PREFIX` / `STONEWAVE_DOC_PREFIX` | según el modelo | Prefijos de pregunta y documento. Se ponen solos para `embeddinggemma` y `nomic-embed-text` |
| `STONEWAVE_GRAPH_ENABLED` | `false` | Activa `generate_graph` |

Se ponen dentro de `"env": { ... }` en la config del servidor.

> Los nombres anteriores (`MEMHOLOGRAFIC_*`) siguen funcionando. Si ya tenías memoria en `~/.memholografic`, al arrancar se copia a `~/.stonewave` y la carpeta anterior queda de respaldo.

> ⚠️ Si cambias el namespace, solo verás los recuerdos guardados con ese namespace.

## Herramientas MCP

| Herramienta | Qué hace |
|---|---|
| `list_memories` | Lista lo que ya se sabe del usuario (se llama al inicio de cada chat) |
| `query_memory` | Busca recuerdos relacionados con un tema |
| `inject_memory` | Guarda un dato duradero (`fijo=true` para identidad) |
| `force_dream` | Sueño: archiva lo que no se usa, clasifica por tema y enlaza recuerdos parecidos |
| `trigger_reflection` | Genera una intuición nueva |
| `generate_graph` | Grafo HTML de entidades y relaciones (requiere `STONEWAVE_GRAPH_ENABLED=true`) |
| `clear_memory` | Borra todo el namespace (irreversible) |

También hay una CLI: `python mem_cli.py inject|query|dream|reflect|clear`.

## Probar

En una conversación nueva con tu agente pregunta: *"¿Qué sabes de mí?"*. Si no recuerda nada:

1. Revisa que Ollama esté corriendo y los modelos descargados (`ollama list`).
2. Revisa que el servidor esté conectado (en verde) en tu agente.
3. Revisa que no hayas puesto un `STONEWAVE_NAMESPACE` o `STONEWAVE_DB` distinto al que usaste al guardar.
4. Prueba el servidor a mano para ver errores: `python mcp_server.py`.

## Errores comunes

- **"Connection closed" / servidor en rojo:** el script falla al arrancar. Ejecútalo a mano (`python mcp_server.py`) y lee el error.
- **`spawn ENOENT`:** tu agente no encuentra `python`. Usa la ruta completa (`where python` en Windows).
- **`No module named 'mcp'`:** instala con `pip install mcp requests numpy` en el mismo Python que usa tu agente.
- **Dice que la memoria está vacía:** casi siempre es otra ruta de BD u otro namespace en la config.

## Estructura

```
mcp_server.py    # servidor MCP (StoneWave)
mem_engine.py    # motor de memoria (inyectar, consultar, soñar, reflexionar)
graph_engine.py  # grafo de entidades (opcional)
mem_cli.py       # CLI
tests/           # test_engine.py (sin Ollama), real_test.py y embed_lab.py (con Ollama), bench_speed.py
docs/            # ideas, experimentos y bitácora de diseño (BITACORA.md)
```

## Estado y pendientes

| Punto | Estado |
|---|---|
| Biblioteca con energía, temas y namespaces | Hecho |
| Sótano y resurrección (lo contradicho no resucita) | Hecho |
| Decaimiento de energía por falta de uso (solo dentro del sueño) | Hecho |
| La consulta baja al sótano si la coincidencia es débil | Hecho |
| Recuerdos fijos (`fijo=true`) | Hecho |
| Velocidad: cache de vectores con numpy, embeddings en lote, guardar sin LLM | Hecho |
| Sueño que ordena y enlaza sin reescribir; las contradicciones bajan al sótano | Hecho |
| Consulta por ondas (5 piedras, crestas y valles, segunda ronda) | Hecho |
| Prueba con Ollama real, prefijos, centrado y umbrales calibrados | Hecho |
| Probar con una memoria real grande (cientos de recuerdos) y reajustar umbrales si hace falta | Pendiente |
| Ver si las piedras ayudan de verdad: hace falta un dataset más grande con preguntas de varios hechos | Pendiente |
| Benchmark de preguntas y respuestas (MemBench). El `benchmark_runner.py` actual mide fusión y quedó viejo | Pendiente |
| API HTTP local para agentes sin MCP (y para compartir una memoria entre varios agentes por red) | Pendiente |
| Búsqueda de puentes semánticos entre temas distintos | Idea |
| Índice vectorial opcional (`faiss-cpu` / `chromadb`) | Idea (con numpy ya aguanta miles) |

**Límites conocidos:** las contradicciones ya no se detectan al guardar sino en el sueño, así que entre un guardado y el siguiente sueño pueden coexistir dos datos que se contradicen. Los umbrales se calibraron con 12 recuerdos y pueden necesitar ajuste con una memoria grande.

La historia completa del diseño y los experimentos está en [`docs/BITACORA.md`](docs/BITACORA.md).
