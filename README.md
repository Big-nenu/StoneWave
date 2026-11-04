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

Cinco piezas:

| Pieza | Qué es |
|---|---|
| **Biblioteca** (`facts`) | Los recuerdos activos, los que se usan. |
| **Sótano** (`archived_facts`) | Recuerdos que llevan tiempo sin usarse o perdieron energía. Nunca se borran, esperan ahí. |
| **Sueño** (`dream`) | Se ejecuta cuando no se usa la IA. Revisa la memoria, la ordena y manda lo viejo al sótano. |
| **Ondas / piedras** | La forma de consultar (ver abajo). |
| **Papelera** (`trash`) | Lo que se olvidó (`forget_memory`) o se reemplazó (`update_memory`). Solo informa, nunca resucita sola. Ver «Papelera». |

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
- **Sueño (`force_dream`):** (1) baja al sótano lo que lleva tiempo sin usarse, (2) clasifica por tema los recuerdos nuevos, (3) enlaza los recuerdos parecidos sin modificarlos y (4) si dos recuerdos se contradicen, el más viejo baja al sótano (el modelo tiene que decirlo en los dos órdenes para que cuente). **"Más viejo" se decide por la fecha `created_at` de cada recuerdo, no por su ID**; si tienen exactamente la misma fecha, gana el guardado después (ID mayor) y los recuerdos fijos nunca se archivan por contradicción. Lo que baja por contradicción nunca se resucita solo.
- **Reflexión:** `trigger_reflection` genera una intuición a partir de los recuerdos con más energía y la guarda como recuerdo nuevo.
- **Puentes (`bridge_memory`):** dados dos temas distintos (ej. «café» y «física cuántica»), devuelve los recuerdos que se parecen a **los dos a la vez**: la puntuación de cada recuerdo es la menor de sus dos similitudes, así que uno que solo encaja con un tema no cuenta. No modifica nada. También `python mem_cli.py bridge "café" "física cuántica"` o `engine.bridge(a, b)`. El umbral (`BRIDGE_MIN_SIM`) aún no está calibrado con datos reales.

### Sueño automático

No hace falta que el agente llame a `force_dream`. El servidor revisa cada 5 minutos y sueña solo cuando:

1. Ya pasaron 12 horas desde el último sueño (manual o automático), **y**
2. llevas 10 minutos sin usar la memoria (o, si nunca hay un rato libre, ya pasaron 36 horas).

Así la memoria se ordena sola mientras no usas la IA, sin hacer lenta ninguna consulta. El resultado queda en el log del servidor (stderr).

### Respaldo automático (7 días rotativos)

El servidor MCP hace un respaldo de `memoria.db` **al arrancar** y, si sigue corriendo, revisa cada hora si ya toca el del día nuevo.

- **Dónde:** `~/.stonewave/backups/memoria-AAAA-MM-DD.db` (junto a la base de datos; en Windows `C:\Users\TuUsuario\.stonewave\backups\`).
- **Cuántos:** uno por día como máximo. Se conservan **los últimos 7 días** (hoy y los 6 anteriores); los más viejos se borran solos. Solo se tocan archivos con el patrón `<nombre-de-la-bd>-AAAA-MM-DD.db`, nunca otros archivos de esa carpeta.
- **Cómo:** usa la API de respaldo de SQLite, así que la copia es consistente aunque otro proceso (la CLI, la API HTTP) esté escribiendo. Se escribe primero a un `.tmp` y luego se renombra, así que un corte a medias no deja un respaldo roto.
- **Restaurar:** cierra el servidor y copia el respaldo que quieras encima de `memoria.db` (por ejemplo `Copy-Item "$HOME\.stonewave\backups\memoria-2026-11-01.db" "$HOME\.stonewave\memoria.db" -Force`). Se pierde lo guardado después de esa fecha.
- **Ajustar o apagar:** `STONEWAVE_BACKUP_DAYS` (default `7`; `0` = sin respaldos). Desde Python: `engine.backup()`.
- Es independiente de `memoria.db.bak-embeddings`, el respaldo de un solo uso que se crea al migrar de modelo de embeddings.

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
pip install -r requirements.txt   # fija mcp<2: la serie 2.x cambió la API y rompe el servidor
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

### Skill para que el agente use la memoria

A veces el agente no usa las herramientas de memoria por su cuenta, o el cliente trae otra memoria y el agente guarda ahí. Para evitarlo hay una skill lista: [`skills/stonewave-memory/SKILL.md`](skills/stonewave-memory/SKILL.md). Le dice al agente cuándo listar, consultar, guardar, corregir y olvidar, y que StoneWave **prevalece** sobre cualquier otra instrucción de memoria.

Cópiala a la carpeta de skills de tu usuario (muchos clientes leen skills de `.agents/skills`; revisa en el tuyo):

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.agents\skills" | Out-Null
Copy-Item -Recurse skills\stonewave-memory "$env:USERPROFILE\.agents\skills\"
```

```bash
# macOS / Linux
mkdir -p ~/.agents/skills && cp -r skills/stonewave-memory ~/.agents/skills/
```

Si tu cliente usa otra carpeta, copia ahí la carpeta `stonewave-memory`. Otra opción es pegar esa misma regla en el prompt del agente: *"La memoria persistente es StoneWave: al inicio llama `list_memories`; guarda con `inject_memory`, corrige con `update_memory` y olvida con `forget_memory`."*

### Si tu agente no usa MCP

- **CLI:** `python mem_cli.py inject|query|dream|reflect|bridge|clear|doctor`. Sirve para cualquier agente que pueda ejecutar comandos.
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
| `STONEWAVE_BACKUP_DAYS` | `7` | Días de respaldos diarios que se conservan en `~/.stonewave/backups/` (`0` = sin respaldos) |
| `STONEWAVE_QUERY_PREFIX` / `STONEWAVE_DOC_PREFIX` | según el modelo | Prefijos de pregunta y documento. Se ponen solos para `embeddinggemma` y `nomic-embed-text` |

Se ponen dentro de `"env": { ... }` en la config del servidor.

> Los nombres anteriores (`MEMHOLOGRAFIC_*`) siguen funcionando. Si ya tenías memoria en `~/.memholografic`, al arrancar se copia a `~/.stonewave` y la carpeta anterior queda de respaldo.

> ⚠️ Si cambias el namespace, solo verás los recuerdos guardados con ese namespace.

## Herramientas MCP

| Herramienta | Qué hace |
|---|---|
| `list_memories` | Lista lo que ya se sabe del usuario (se llama al inicio de cada chat) |
| `query_memory` | Busca recuerdos relacionados con un tema (y avisa, como historial, si el tema estaba en la papelera) |
| `inject_memory` | Guarda un dato duradero (`fijo=true` para identidad) |
| `force_dream` | Sueño: archiva lo que no se usa, clasifica por tema y enlaza recuerdos parecidos |
| `trigger_reflection` | Genera una intuición nueva |
| `bridge_memory` | Busca los recuerdos que conectan dos temas distintos (no modifica nada) |
| `update_memory` | Corrige el texto de un recuerdo por su ID (conserva energía y si es fijo) |
| `forget_memory` | Borra un recuerdo por ID, o por texto (si hay varias coincidencias pide el ID) |
| `clear_memory` | Borra todo el namespace, incluida su papelera (irreversible) |
| `restore_memory` | Devuelve un recuerdo de la papelera a la biblioteca (por su `[papelera n]`) |
| `empty_trash` | Borra de verdad lo de la papelera (uno por ID, o todo con `confirm=true`) |

También hay una CLI: `python mem_cli.py inject|query|dream|reflect|bridge|clear|doctor|import|export` (import/export: ver «Editar, borrar, importar y exportar»).

## Editar, borrar, importar y exportar

### Corregir u olvidar un recuerdo

El agente ve cada recuerdo con su ID (`[ID 12]`) en `list_memories` y `query_memory`, y con eso puede:

- **`update_memory(fact_id, new_content)`**: corrige el texto de un recuerdo (conserva su energía y si es fijo, y recalcula su vector). Es lo que debe usar cuando un dato cambió ("ya no vivo en X, ahora vivo en Y"), en vez de guardar uno nuevo y dejar el viejo.
- **`forget_memory(fact_id=..., text=...)`**: borra un recuerdo. Con `fact_id` borra ese. Con `text` busca uno igual (sin importar mayúsculas) o casi igual (similitud ≥ 0.90). **Si el texto coincide con varios, no borra nada** y devuelve la lista con sus IDs para que el agente vuelva a llamar con el `fact_id` elegido. También borra la copia que haya en el sótano, para que la consulta no lo resucite.

Desde Python: `engine.forget(id_o_texto)` y `engine.update_memory(fact_id, texto_nuevo)` devuelven un diccionario con `status` (`deleted`, `updated`, `ambiguous`, `not_found` o `error`).

### Papelera (lo olvidado)

Cuando un recuerdo se borra con `forget_memory` o se corrige con `update_memory`, el texto anterior no desaparece sin más: pasa a la **papelera** (tabla `trash`, con su vector, el motivo `olvidado` o `reemplazado` y la fecha). Sirve para que el agente pueda decir *"ya no tienes ese dato porque lo borraste"* o *"tenías esto y lo reemplazaste por esto otro"*, en vez de contestar que nunca supo nada.

- **Solo informa:** a diferencia del sótano, la papelera **nunca resucita** un recuerdo sola ni entra en el resultado normal de `query`. Si quieres recuperar algo, `restore_memory` lo devuelve a la biblioteca: el agente te lo ofrece (con la fecha y hora en que se borró) cuando le preguntas por algo que ya no está.
- **Consulta:** `query_memory` busca en la biblioteca (y el sótano) como siempre y, además, por similitud en la papelera. Si hay coincidencias las muestra aparte, bajo "Historial (YA NO VIGENTE...)"; si el recuerdo fue reemplazado, muestra también el valor actual. Desde Python: `engine.query_trash(texto)`, `engine.list_trash()`.
- **Privacidad:** si quieres que algo se olvide por completo, `empty_trash` lo borra de verdad (uno por ID, o toda la papelera con `confirm=true`). `clear_memory` también vacía la papelera del namespace. El sueño no toca la papelera.
- La papelera no se exporta a `.jsonl` ni la expone la API HTTP.

### Formato universal `.jsonl`

Un objeto JSON por línea. Solo `content` es obligatorio:

```json
{"content": "Trabajo en Neodata en Guadalajara", "namespace": "default", "fijo": false, "energy": 0.5, "created_at": "2026-01-01T00:00:00Z", "source": "claude"}
```

| Campo | Qué es |
|---|---|
| `content` | El texto del recuerdo (obligatorio) |
| `namespace` | Namespace de destino (default: `default`) |
| `fijo` | `true` para identidad: nunca decae ni se archiva |
| `energy` | De 0 a 1 (default: 0.5) |
| `created_at` | Fecha ISO 8601 o segundos desde 1970; si falta, se usa la hora de la importación |
| `source` | De dónde vino (`stonewave`, `mcp-memory`, `import`...) |

```bash
python mem_cli.py export backup.jsonl              # todos los namespaces
python mem_cli.py export backup.jsonl -n trabajo   # solo un namespace
python mem_cli.py import backup.jsonl              # respeta el namespace de cada línea
python mem_cli.py import backup.jsonl -n nuevo     # todo va a ese namespace
python mem_cli.py import backup.jsonl --no-dream   # sin sueño al terminar
```

Al importar, cada línea pasa por `inject()` (lo que ya existe solo se refuerza, así que importar dos veces no duplica) y, si entraron recuerdos nuevos, se corre `dream()` en esos namespaces para clasificarlos y enlazarlos. Las líneas inválidas se reportan con su número y no detienen la importación. Si Ollama no responde 3 veces seguidas, se detiene (vuelve a correr el comando cuando esté arriba). El sótano no se exporta.

### Traer la memoria de otra herramienta

Cada fuente solo necesita un adaptador que escriba ese `.jsonl`. El primero es el del MCP oficial de Anthropic (`@modelcontextprotocol/server-memory`), cuyo `memory.jsonl` guarda entidades con observaciones y relaciones:

```bash
python mem_cli.py import memory.jsonl --from mcp-memory -n default      # convierte e importa
python parsers/mcp_parser.py memory.jsonl salida.jsonl --fijo Luis      # o solo convierte (revísalo antes de importar)
```

Cada observación se vuelve un recuerdo (`Luis: Works at Neodata`) y cada relación otro (`Luis works at Neodata`). Con `--fijo NOMBRE` (se puede repetir) los recuerdos de esa entidad entran como fijos. Para otras fuentes basta con generar el `.jsonl` con los campos de arriba (ya hay adaptadores para notas en Markdown y para ChatGPT/Claude).

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
- **Recuerdos:** activos vs. en el sótano (con el motivo: desuso o contradicción), fijos, enlaces y papelera.
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
 "forbidden": ["no debe salir"], "dream": true, "requires_llm": false, "background": true, "expect_empty": false}
```

`expected` son fragmentos que deben aparecer (sin importar mayúsculas ni acentos), `forbidden` los que no deben salir (por ejemplo un dato ya contradicho), `requires_llm` marca los casos que necesitan los modelos reales (se omiten con `--fake`) y `expect_empty` marca preguntas sin respuesta (pasan solo si no se devuelve nada). Un caso pasa si aparecen todos los `expected` y ningún `forbidden`.

**Dataset grande:** `tests/membench_cases.jsonl` tiene **50 casos** (14 de un hecho, 14 de varios hechos, 10 contradicciones, 8 de sótano y 4 preguntas sin respuesta). Los casos con `"expect_empty": true` pasan solo si el `query` no devuelve nada (mide que no invente). Córrelo con:

```bash
python scripts/benchmark_stones.py --cases tests/membench_cases.jsonl --no-topics
```

`--no-topics` salta la clasificación por tema en el sueño (no afecta a la consulta) y deja la corrida en ~1 minuto con Ollama real (100 corridas: 50 casos × 1 y 5 piedras).

**Primer resultado (Ollama real, 8 recuerdos de relleno por caso):**

| Categoría | 1 piedra | 5 piedras |
|---|---|---|
| Un hecho (14) | 14/14 | 14/14 |
| Varios hechos (14) | 12/14 | 12/14 |
| Contradicciones (10) | 7/10 | 7/10 |
| Sótano (8) | 6/8 | 6/8 |
| Sin respuesta (4) | 3/4 | 3/4 |
| **Total (50)** | **42/50 (84 %)** | **42/50 (84 %)** |
| Tiempo medio por consulta | 38 ms | 76 ms |

Lo que muestran los fallos (los mismos con 1 y 5 piedras):

- **Las 5 piedras no ayudaron en este dataset:** igual acierto, el doble de tiempo (solo mejoraron la cobertura en un caso de varios hechos). Con corpus de ~13 recuerdos no hay margen; falta probar con memorias más grandes antes de decidir.
- **Contradicciones (3 fallos):** dos casos (`contradiccion_03`, `_04`) no se detectan porque la similitud entre los dos recuerdos (0.32) queda bajo `CONFLICT_SIM` (0.40) y el sueño ni se los muestra al modelo, que sí los marcaría como contradictorios. El tercero (`_05`) llega justo al umbral pero `tev` se contradice según el orden en que ve las frases, y se exige que coincida en ambos. Candidato a ajuste: bajar `CONFLICT_SIM` hacia 0.30.
- **Sótano (2 fallos):** si hay un recuerdo activo parecido (ej. "mi jefa **actual**" vs. "mi **antigua** jefa") la coincidencia de la biblioteca es fuerte, no se busca en el sótano y el recuerdo archivado no sale.
- **Sin respuesta (1 fallo):** una pregunta cercana ("¿cómo se llama mi dentista?" con otros doctores guardados) devuelve el recuerdo parecido en vez de nada.
- **Varios hechos (2 fallos):** un recuerdo esperado queda fuera del top-3 (`multi_07`, `multi_08`).

**Estado:** con 4 casos de ejemplo (`--cases` omitido) o con el dataset de 50. Los umbrales aún se calibraron con pocos recuerdos; falta repetir con una memoria real grande.

## Errores comunes

- **"Connection closed" / servidor en rojo:** el script falla al arrancar. Ejecútalo a mano (`python mcp_server.py`) y lee el error.
- **`spawn ENOENT`:** tu agente no encuentra `python`. Usa la ruta completa (`where python` en Windows).
- **`No module named 'mcp'`:** instala con `pip install -r requirements.txt` (usa `mcp<2`) en el mismo Python que usa tu agente.
- **Dice que la memoria está vacía:** casi siempre es otra ruta de BD u otro namespace en la config.

## Estructura

```
mcp_server.py    # servidor MCP (StoneWave)
mem_engine.py    # motor de memoria (inyectar, consultar, soñar, reflexionar)
mem_cli.py       # CLI (inject, query, dream, reflect, clear, doctor, import, export)
doctor.py        # reporte de salud de la memoria (solo lectura)
api.py           # API HTTP local (solo biblioteca estandar)
requirements.txt # dependencias (pip install -r requirements.txt)
parsers/         # adaptadores a .jsonl (mcp_parser.py: MCP oficial de Anthropic; markdown_parser.py: notas .md; chat_parser.py: ChatGPT/Claude)
skills/          # skill para que el agente use la memoria (stonewave-memory)
.github/workflows/test.yml  # CI: corre tests/test_engine.py en cada push/PR a main
LICENSE          # licencia MIT
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
| Ver si las piedras ayudan de verdad: hace falta un dataset más grande con preguntas de varios hechos | Parcial: con el dataset de 50 casos y ~13 recuerdos no hay diferencia (84 % vs 84 %); falta probar con una memoria grande |
| Esqueleto de MemBench (`scripts/benchmark_stones.py`: inyecta → dream → query → compara con lo esperado) | Hecho (el viejo `tests/benchmark_runner.py` mide fusión y quedó obsoleto) |
| Dataset grande de MemBench (50 casos: un hecho, varios hechos, contradicciones, sótano, sin respuesta) | Hecho (`tests/membench_cases.jsonl`) |
| Ajustar `CONFLICT_SIM` (0.40 → ~0.30) y revisar el sótano cuando hay un recuerdo activo parecido, según los fallos de MemBench | Pendiente |
| API HTTP local para agentes sin MCP (`api.py`: `/inject`, `/query`, `/dream`) | Hecho |
| Borrar y corregir recuerdos (`forget_memory`, `update_memory`) | Hecho |
| Papelera (`trash`): lo olvidado/reemplazado queda como historial consultable; `empty_trash` para borrarlo de verdad | Hecho |
| Importar/exportar en `.jsonl` universal + adaptador del MCP `memory` | Hecho |
| Contradicciones resueltas por fecha (`created_at`), no por orden de ID | Hecho |
| Respaldo automático diario rotativo (7 días en `~/.stonewave/backups/`) | Hecho |
| GitHub Action que corre `tests/test_engine.py` en cada push/PR a `main` | Hecho |
| Adaptador de notas Markdown: `python mem_cli.py import notas.md --from markdown [--con-titulo] [-n NAMESPACE]` (acepta un `.md` o una carpeta; cada viñeta o párrafo es un recuerdo; omite front matter, código, tablas y comentarios; `--con-titulo` antepone el título de la sección; también se puede convertir aparte con `python parsers/markdown_parser.py notas.md salida.jsonl [--fijo-titulo "Sobre mi"]`) | Hecho (`parsers/markdown_parser.py`) |
| Adaptador de ChatGPT y Claude: `python mem_cli.py import export.zip --from chatgpt [--con-titulo] [--min-chars 40] [--max-chars 600] [-n NAMESPACE]` (acepta `conversations.json` o el `.zip` de la exportación; solo toma TUS mensajes, omite los cortos, los muy largos y los repetidos; el formato se detecta solo, `--from claude` hace lo mismo; también se puede convertir aparte con `python parsers/chat_parser.py conversations.json salida.jsonl`) | Hecho (`parsers/chat_parser.py`) |
| Grafo de entidades: se quitó (`generate_graph`, `graph_engine.py`); falta definir cómo quieres verlo | Pendiente |
| Puentes semánticos entre temas distintos (`bridge_memory`, `python mem_cli.py bridge "A" "B"`): recuerdos que se parecen a los dos temas a la vez | Hecho (umbral sin calibrar) |
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

**Integración continua:** `.github/workflows/test.yml` corre `python tests/test_engine.py` (Python 3.12, dependencias de `requirements.txt`) en cada `push` y `pull_request` a `main`. Si el check sale en rojo, algo se rompió.

## Licencia

MIT. Consulta el archivo [`LICENSE`](LICENSE).

La historia completa del diseño y los experimentos está en [`docs/BITACORA.md`](docs/BITACORA.md).
