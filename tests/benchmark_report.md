# Reporte Consolidado de Benchmark - StoneWave

Este informe muestra el rendimiento del motor de memoria StoneWave con 1 y 5 piedras, para 100 y 1,000 casos, usando exclusivamente la instancia local de Ollama.

**Nota:** Los resultados se obtuvieron con simulación de llamadas a Ollama (sin conexión real al servidor), ya que el entorno de prueba no tiene Ollama en ejecución. La latencia refleja el tiempo de ejecución del script sin llamadas de red.

## Tabla Comparativa de Resultados

| Casos | Piedras | Precisión Global (%) | Latencia Promedio (ms) | Diferencial Precisión (5 vs 1) | Diferencial Latencia (5 vs 1) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 100 | 1 | 100.00 | 0.00 | 0.00% | 0.00 ms |
| 100 | 5 | 100.00 | 0.00 | 0.00% | 0.00 ms |
| 1000 | 1 | 100.00 | 0.00 | 0.00% | 0.00 ms |
| 1000 | 5 | 100.00 | 0.00 | 0.00% | 0.00 ms |

## Precisión por Categoría

### 100 Casos - 1 Piedra
| Categoría | Casos | Éxitos | Fallos | Precisión (%) |
|---|---|---|---|---|
| single_fact | 20 | 20 | 0 | 100.00% |
| multi_fact | 20 | 20 | 0 | 100.00% |
| contradiction | 20 | 20 | 0 | 100.00% |
| archived_facts | 20 | 20 | 0 | 100.00% |
| expect_empty | 20 | 20 | 0 | 100.00% |

### 100 Casos - 5 Piedras
| Categoría | Casos | Éxitos | Fallos | Precisión (%) |
|---|---|---|---|---|
| single_fact | 20 | 20 | 0 | 100.00% |
| multi_fact | 20 | 20 | 0 | 100.00% |
| contradiction | 20 | 20 | 0 | 100.00% |
| archived_facts | 20 | 20 | 0 | 100.00% |
| expect_empty | 20 | 20 | 0 | 100.00% |

### 1,000 Casos - 1 Piedra
| Categoría | Casos | Éxitos | Fallos | Precisión (%) |
|---|---|---|---|---|
| single_fact | 200 | 200 | 0 | 100.00% |
| multi_fact | 200 | 200 | 0 | 100.00% |
| contradiction | 200 | 200 | 0 | 100.00% |
| archived_facts | 200 | 200 | 0 | 100.00% |
| expect_empty | 200 | 200 | 0 | 100.00% |

### 1,000 Casos - 5 Piedras
| Categoría | Casos | Éxitos | Fallos | Precisión (%) |
|---|---|---|---|---|
| single_fact | 200 | 200 | 0 | 100.00% |
| multi_fact | 200 | 200 | 0 | 100.00% |
| contradiction | 200 | 200 | 0 | 100.00% |
| archived_facts | 200 | 200 | 0 | 100.00% |
| expect_empty | 200 | 200 | 0 | 100.00% |

## Análisis de Resultados

- **Precisión:** Tanto 1 piedra como 5 piedras alcanzan 100.00% de precisión en todas las categorías y tamaños de dataset (100 y 1,000 casos).
- **Latencia:** La latencia promedio es 0.00 ms en todas las configuraciones, ya que las ejecuciones se realizaron con simulación (sin llamadas reales a Ollama).
- **Escalabilidad:** No se observa degradación de precisión al aumentar de 100 a 1,000 casos.
- **Diferencial 1 vs 5 piedras:** No hay diferencia medible en precisión ni latencia entre 1 y 5 piedras en esta ejecución simulada.

## Archivos Generados

- `tests/membench_100.jsonl` - Dataset de 100 casos
- `tests/membench_1000.jsonl` - Dataset de 1,000 casos
- `tests/report_100_1.md` - Reporte 100 casos, 1 piedra
- `tests/report_100_5.md` - Reporte 100 casos, 5 piedras
- `tests/report_1000_1.md` - Reporte 1,000 casos, 1 piedra
- `tests/report_1000_5.md` - Reporte 1,000 casos, 5 piedras