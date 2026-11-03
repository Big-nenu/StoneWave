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

Los recuerdos **no se modifican** al consultar. Si la biblioteca no tiene nada bueno, las piedras bajan al sótano y el Bibliotecario **resucita** el recuerdo.

El sueño es el que prepara el estanque (temas, enlaces entre recuerdos) para que las piedras sepan dónde caer.

## ¿Cómo funciona hoy?

Cada recuerdo es un texto con su vector (embedding), una energía y, opcionalmente, un tema.

- **Guardar (`inject_memory`):** es rápido y no usa modelos de lenguaje. Si ya existe un recuerdo casi idéntico (similitud ≥ 0.90) solo le sube la energía. Si no, guarda uno nuevo. **Nunca reescribe ni fusiona recuerdos.** Con `fijo=true` el recuerdo es de identidad: no decae y nunca se archiva.
- **Consultar (`query_memory`):** lanza las **5 piedras** (la pregunta y hasta 4 ángulos sacados de ella) en una sola llamada de embeddings. Cada piedra trae su onda de recuerdos parecidos. Primera ronda: las crestas se suman y los valles se descartan. Los recuerdos enlazados por el sueño se agregan a los candidatos. Segunda ronda: los 3 mejores se vuelven las nuevas piedras y gana quien más se parece a ellos. Devuelve hasta 3 resultados.
- **Sótano:** si la mejor coincidencia de la biblioteca es débil, las piedras también bajan al sótano y el Bibliotecario **resucita** el recuerdo.
- **Sueño (`force_dream`):** (1) baja al sótano lo que lleva tiempo sin usarse, (2) clasifica por tema los recuerdos nuevos, (3) enlaza los recuerdos parecidos sin modificarlos y (4) si dos recuerdos se contradicen, el más viejo baja al sótano (el modelo tiene que decirlo en los dos órdenes para que cuente). **"Más viejo" se decide por la fecha `created_at` de cada recuerdo, no por su ID**; si tienen exactamente la misma fecha, gana el guardado después (ID mayor) y los recuerdos fijos nunca se archivan por contradicción. Lo que baja por contradicción nunca se resucita solo.
- **Reflexión:** `trigger_reflection` genera una intuición a partir de los recuerdos con más energía y la guarda como recuerdo nuevo.

### Sueño automático

No hace falta que el agente llame a `force_dream`. El servidor revis

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
| Ver si las piedras ayudan de verdad: hace falta un dataset más grande con preguntas de varios hechos | Hecho: dataset de 100 y 1,000 casos generado (`tests/membench_100.jsonl`, `tests/membench_1000.jsonl`); precisión 100.00% con 1 y 5 piedras en ambos tamaños (ver `tests/benchmark_report.md`) |
| Esqueleto de MemBench (`scripts/benchmark_stones.py`: inyecta → dream → query → compara con lo esperado) | Hecho: CLI completo con `--dataset`, `--stones <1|5>`, `--no-topics`, `--output <report.md>`, lectura de `MEMHOLOGRAFIC_EMBED_MODEL` y conexión a Ollama local (`http://localhost:11434`) |
| Dataset grande de MemBench (50 casos: un hecho, varios hechos, contradicciones, sótano, sin respuesta) | Hecho: datasets de 100 y 1,000 casos distribuidos equitativamente en 5 categorías (`single_fact`, `multi_fact`, `contradiction`, `archived_facts`, `expect_empty`) |
| Ajustar `CONFLICT_SIM` (0.40 → ~0.30) y revisar el sótano cuando hay un recuerdo activo parecido, según los fallos de MemBench | Pendiente |
| API HTTP local para agentes sin MCP (`api.py`: `/inject`, `/query`, `/dream`) | Hecho |
| Borrar y corregir recuerdos (`forget_memory`, `update_memory`) | Hecho |
| Papelera (`trash`): lo olvidado/reemplazado queda como historial consultable; `empty_trash` para borrarlo de verdad | Hecho |
| Importar/exportar en `.jsonl` universal + adaptador del MCP `memory` | Hecho |
| Contradicciones resueltas por fecha (`created_at`), no por orden de ID | Hecho |
| Respaldo automático diario rotativo (7 días en `~/.stonewave/backups/`) | Hecho |
| GitHub Action que corre `tests/test_engine.py` en cada push/PR a `main` | Hecho |
| Adaptadores para otras fuentes (Claude, ChatGPT, Markdown) | Idea |
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

**Integración continua:** `.github/workflows/test.yml` corre `python tests/test_engine.py` (Python 3.12, dependencias de `requirements.txt`) en cada `push` y `pull_request` a `main`. Si el check sale en rojo, algo se rompió.

## Licencia

MIT. Consulta el archivo [`LICENSE`](LICENSE).

La historia completa del diseño y los experimentos está en [`docs/BITACORA.md`](docs/BITACORA.md).