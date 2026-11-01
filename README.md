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

### Sueño automático

No hace falta que el agente llame a `force_dream`. El servidor revisa cada 5 minutos y sueña solo cuando:

1. Ya pasaron 12 horas desde el último sueño (manual o automático), **y**
2. llevas 10 minutos sin usar la memoria (o, si nunca hay un rato libre, ya pasaron 36 horas).

Así la memoria se ordena sola mientras no usas la IA, sin hacer lenta ninguna consulta. El resultado queda en el log del servidor (stderr).

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

- **CLI:** `python mem_cli.py inject|query|dream|reflect|clear|doctor`. Sirve para cualquier agente que pueda ejecutar comandos.
- **Python:** `from mem_engine import StoneWaveEngine` para usarlo dentro de tu propio agente.
- **API HTTP local:** `python api.py` (solo biblioteca estándar, no instala nada). Ver la sección de abajo.

Varios agentes pueden usar la misma memoria a la vez: la cache de vectores se actualiza sola cuando otro proceso cambia la base de datos.

### API HTTP local

Para agentes o programas que no hablan MCP (o para que varios agentes compartan la misma memoria):

```bash
python api.py                 # escucha en http://127.0.0.1:8765
python api.py --port 9000     # otro puerto
```

| Endpoint | Cuerpo (JSON) | Responde |
|---|---|---|
| `POST /inject` | `{"content": "...", "energy": 0.5, "namespace": "default", "fijo": false}` | `{"ok": true, "result": "..."}` |
| `POST /query` | `{"text": "...", "namespace": "default"}` | `{"ok": true, "results": [{"sim", "id", "energy", "content"}]}` (hasta 3) |
| `POST /dream` | `{"namespace": "default"}` | `{"ok": true, "result": "..."}` |
| `GET /health` | — | estado y número de recuerdos |

```bash
curl -X POST http://127.0.0.1:8765/inject -H "Content-Type: application/json" -d '{"content": "Mi perfume favorito es Versace Oud Noir"}'
curl -X POST http://127.0.0.1:8765/query  -H "Content-Type: application/json" -d '{"text": "¿Qué perfume me gusta?"}'
```

(En PowerShell escribe `curl.exe` en vez de `curl`.)

**Seguridad:** por defecto solo escucha en `127.0.0.1`, exige `Content-Type: application/json` y rechaza peticiones cuyo `Host` no sea local (protege contra páginas web maliciosas). Si la abres a tu red con `--host 0.0.0.0`, define `STONEWAVE_API_TOKEN` y manda `Authorization: Bearer <token>`.

La API no sueña sola: llama a `/dream` de vez en cuando o deja corriendo el servidor MCP, que sí tiene sueño automático.

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
| `STONEWAVE_API_HOST` / `STONEWAVE_API_PORT` | `127.0.0.1` / `8765` | Dónde escucha `api.py` |
| `STONEWAVE_API_TOKEN` | (vacío) | Si lo defines, la API exige `Authorization: Bearer <token>` |
| `STONEWAVE_AUTO_DREAM` | `true` | Sueño automático en segundo plano |
| `STONEWAVE_DREAM_HOURS` | `12` | Cada cuántas horas conviene soñar (`0` = nunca solo) |
| `STONEWAVE_DREAM_IDLE_MIN` | `10` | Minutos sin usar la memoria antes de soñar solo |
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

También hay una CLI: `python mem_cli.py inject|query|dream|reflect|clear|doctor`.

## Probar

En una conversación nueva con tu agente pregunta: *"¿Qué sabes de mí?"*. Si no recuerda nada:

1. Revisa que Ollama esté corriendo y los modelos descargados (`ollama list`).
2. Revisa que el servidor esté conectado (en verde) en tu agente.
3. Revisa que no hayas puesto un `STONEWAVE_NAMESPACE` o `STONEWAVE_DB` distinto al que usaste al guardar.
4. Prueba el servidor a mano para ver errores: `python mcp_server.py`.

## Doctor: revisar la salud de la memoria

```bash
python mem_cli.py doctor            # namespace "default"
python mem_cli.py doctor -n trabajo # otro namespace
```

Es de **solo lectura** (no abre el motor ni modifica nada) e imprime un reporte rápido:

- **Base de datos:** ruta, conexión, tamaño de `memoria.db` y chequeo de integridad.
- **Recuerdos:** activos vs. en el sótano (con el motivo: desuso o contradicción), fijos y enlaces.
- **Ollama:** si responde, y si los modelos de embeddings (`embeddinggemma-2:270m`) y de decisión (`tev1:0.8b`) están instalados y contestan de verdad, con su tiempo de respuesta.
- **Sueño:** fecha y hora del último ciclo (y si el automático está activado).
- **Entorno:** si numpy está instalado.

Termina con código `0` si todo está bien (o solo hay avisos) y `1` si hay algún problema grave, así que también sirve en scripts.

```
=== StoneWave Doctor ===

Base de datos
  [OK] Conexion OK, tamano 176.0 KB
  [OK] Integridad: ok

Recuerdos (namespace 'default')
  [OK] Activos (biblioteca): 6 (fijos: 1)
  [OK] En el sotano: 0

Ollama (local)
  [OK] Embeddings: embeddinggemma-2:270m vectores de 768 dimensiones (2337 ms)
  [OK] Decision: tev1:0.8b responde (1649 ms)
  ...
```

### Soluciones sugeridas

Si el doctor encuentra un fallo **grave**, al final imprime una sección **SOLUCIONES SUGERIDAS** con los comandos exactos para repararlo (no ejecuta nada por ti; los comandos se adaptan a tu sistema: Windows, macOS o Linux). Los avisos que no son graves salen aparte, en **RECOMENDACIONES (no graves)**.

| Problema | Qué sugiere |
|---|---|
| Ollama apagado | Cómo arrancarlo (`ollama serve`, `brew services restart ollama` o `systemctl restart ollama`) y cómo comprobar que responde (`curl http://localhost:11434/api/tags`) |
| Falta un modelo | `ollama pull <modelo>` y `ollama list` para confirmarlo |
| Modelo instalado que no responde | `ollama ps`, `ollama stop <modelo>`, volver a bajarlo, dónde ver el log de Ollama (y actualizar Ollama si da HTTP 404) |
| Base de datos que no abre o está dañada | Revisar procesos y permisos; guardar una copia del archivo dañado; restaurar desde `memoria.db.bak-embeddings` o `~/.memholografic/memoria.db` si existen (pone el comando exacto), y si no hay respaldos, `sqlite3 ... ".recover"` |
| Faltan tablas (base de datos vieja) | El comando para que el motor las vuelva a crear |
| numpy no instalado (aviso) | `pip install numpy` en el mismo Python que ejecuta el doctor |

```
[XX] No se pudo abrir la base de datos: file is not a database

============================================================
SOLUCIONES SUGERIDAS
============================================================

1. No se pudo abrir la base de datos: file is not a database
     La base de datos esta danada.
     Antes de tocar nada, guarda una copia del archivo actual:
     $ Copy-Item "...\memoria.db" "...\memoria.db.danada" -Force
     No encontre respaldos automaticos. Intenta recuperar lo que se pueda:
     $ sqlite3 "...\memoria.db" ".recover" | sqlite3 "...\memoria.db.recuperada"
```

Las pruebas del doctor (`tests/test_doctor.py`) simulan cada fallo sin necesitar Ollama.

## Benchmark (MemBench)

Mide si el motor **responde bien**, no solo si corre. Cada caso, en una base de datos temporal (no toca tu memoria):

1. inyecta los recuerdos del caso (más 8 recuerdos de relleno para que el corpus se parezca a una memoria real),
2. envejece los que lo pidan (para probar el sótano),
3. llama a `dream`,
4. hace un `query` con la pregunta,
5. compara lo devuelto (top-3) contra la respuesta esperada.

```bash
python scripts/benchmark_stones.py                       # compara 1 piedra vs 5 piedras (necesita Ollama)
python scripts/benchmark_stones.py --stones 5            # solo 5 piedras
python scripts/benchmark_stones.py --cases casos.jsonl   # tus propios casos
python scripts/benchmark_stones.py --fake                # sin Ollama: solo prueba que el benchmark corre
python scripts/benchmark_stones.py --json salida.json    # guarda los resultados
python scripts/benchmark_stones.py --min-pass 0.8        # código de salida 1 si acierta menos del 80 %
```

Formato de un caso (una línea JSON por caso en el `.jsonl`):

```json
{"id": "multi_01", "category": "multi",
 "facts": ["texto", {"text": "otro texto", "pinned": false, "age_days": 200}],
 "question": "¿...?", "expected": ["fragmento1", "fragmento2"],
 "forbidden": ["no debe salir"], "dream": true, "requires_llm": false, "background": true}
```

`expected` son fragmentos que deben aparecer (sin importar mayúsculas ni acentos), `forbidden` los que no deben salir (por ejemplo un dato ya contradicho) y `requires_llm` marca los casos que necesitan los modelos reales (se omiten con `--fake`). Un caso pasa si aparecen todos los `expected` y ningún `forbidden`.

**Estado:** es un esqueleto con 4 casos de ejemplo (un hecho, varios hechos, contradicción y sótano). Con Ollama real: 1 piedra acierta 3/4 (falla la pregunta de varios hechos) y 5 piedras 4/4, pero la muestra es muy chica; falta el dataset grande.

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
mem_cli.py       # CLI (inject, query, dream, reflect, clear, doctor)
doctor.py        # reporte de salud de la memoria (solo lectura)
api.py           # API HTTP local (solo biblioteca estandar)
scripts/         # benchmark_stones.py: MemBench (esqueleto de benchmark)
tests/           # test_engine.py, test_api.py, test_doctor.py y test_benchmark.py (sin Ollama), real_test.py y embed_lab.py (con Ollama), bench_speed.py
docs/            # ideas, experimentos y bitácora de diseño (BITACORA.md)
archive/v0.1/    # prototipos y scripts de la primera version (historial, sin mantener)
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
| Sueño automático en segundo plano (cuando toca y no se usa la memoria) | Hecho |
| Comando `doctor`: reporte de salud (BD, sótano, Ollama, último sueño) | Hecho |
| Doctor solucionador: si hay un fallo grave imprime las soluciones sugeridas (comandos exactos) | Hecho |
| Limpieza del repo: prototipos y scripts viejos movidos a `archive/v0.1/` | Hecho |
| Probar con una memoria real grande (cientos de recuerdos) y reajustar umbrales si hace falta | Pendiente |
| Ver si las piedras ayudan de verdad: hace falta un dataset más grande con preguntas de varios hechos | Pendiente |
| Esqueleto de MemBench (`scripts/benchmark_stones.py`: inyecta → dream → query → compara con lo esperado) | Hecho (el viejo `tests/benchmark_runner.py` mide fusión y quedó obsoleto) |
| Dataset grande de MemBench (unos 50 casos: varios hechos, contradicciones, sótano) | Pendiente |
| API HTTP local para agentes sin MCP (`api.py`: `/inject`, `/query`, `/dream`) | Hecho |
| Búsqueda de puentes semánticos entre temas distintos | Idea |
| Índice vectorial opcional (`faiss-cpu` / `chromadb`) | Idea (con numpy ya aguanta miles) |

**Límites conocidos:** las contradicciones ya no se detectan al guardar sino en el sueño, así que entre un guardado y el siguiente sueño pueden coexistir dos datos que se contradicen. Los umbrales se calibraron con 12 recuerdos y pueden necesitar ajuste con una memoria grande.

## Para contribuir

**Regla de documentación:** cualquier cambio de arquitectura, función nueva o script agregado se documenta en este README, en su sección correspondiente, **en el mismo commit**. Si no está en el README, no está terminado.

Antes de subir cambios, corre las pruebas (ninguna de estas necesita Ollama):

```bash
python tests/test_engine.py
python tests/test_api.py
python tests/test_doctor.py
python tests/test_benchmark.py
```

Con Ollama encendido también: `python tests/real_test.py` y `python scripts/benchmark_stones.py`.

La historia completa del diseño y los experimentos está en [`docs/BITACORA.md`](docs/BITACORA.md).
