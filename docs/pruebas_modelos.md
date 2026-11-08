# Pruebas de modelos (8 de noviembre de 2026)

Se compararon los modelos que ya estaban instalados en Ollama con casos en español, usando **los mismos prompts que el motor**.
Se corre con `python tests/compare_models.py` (ver README). Los casos están en `tests/casos/`.

**Límites:** son pocos casos (30 pares, 14 de respaldo, 12 de conclusiones), una sola corrida por modelo, y los modelos no son
deterministas. Las etiquetas de los pares "ambiguos" son mías y son discutibles. Sirve para elegir, no para medir con precisión.
No estaban instalados `tev1:4b` ni `d3:4b`, así que no se probaron.

## 1. Modelo de decisión (contradicciones)

30 pares de recuerdos: 10 que sí se contradicen, 12 que se complementan y 8 ambiguos (podrían ser dos cosas distintas).
Se marca como contradicción solo si el modelo lo dice en los dos órdenes, igual que el sueño. Un falso positivo manda un recuerdo válido al sótano.

| Modelo | Detecta las 10 | Falsos (12 complementarios) | Falsos (8 ambiguos) | Respaldo de conclusiones (14) |
|---|---|---|---|---|
| **d1:3b** (el de hoy) | 10/10 | 0/12 | 3/8 | 12/14 |
| d3:2b | 10/10 | 0/12 | 4/8 | 9/14 (rechaza 4 buenas) |
| tev1:0.8b | 10/10 | 5/12 | 5/8 | 13/14 |

Con un solo orden habrían marcado de más 5 (`d1:3b`), 11 (`tev1:0.8b`) y 4 (`d3:2b`) pares: la regla de los dos órdenes ayuda mucho, pero no arregla a `tev1:0.8b`.
Los tres tardan unos 55 ms por par.

**Conclusión:** `d1:3b` se queda como modelo de decisión.

## 2. Verificador de conclusiones

Las conclusiones de tres modelos generativos se volvieron a verificar con cada modelo de decisión (regla del sueño: la mayoría de los recuerdos
la respalda y ninguno la contradice).

| Verificador | Conclusiones buenas que pasan (de 8) | Conclusiones inventadas que pasan |
|---|---|---|
| **d1:3b** | 3 a 5 | 0 a 1 de cada 1 a 3 |
| d3:2b | 2 a 3 | 0 |
| tev1:0.8b | 8 | 5 de 6: dice que respalda **todo** |

`tev1:0.8b` aprobó casi todo en la prueba corta de respaldo, pero con conclusiones abstractas dice "respalda" a todo: no sirve de verificador.
`d1:3b` es el mejor equilibrio. Es algo estricto: tira cerca de la mitad de las conclusiones buenas, lo cual es aceptable porque las hipótesis nacen con poca energía.

## 3. Modelo generativo (conclusiones)

8 grupos de recuerdos con una conclusión clara y 4 sin ninguna posible. Verificador: `d1:3b`.
"Acierta" mide que mencione el tema esperado (por palabras clave, algo estricto: algunas conclusiones buenas cuentan como fallo).

| Modelo | Acierta (8) | Acierta y pasa (8) | Dijo NINGUNA bien (4) | Inventó y pasó la verificación | Tiempo por conclusión |
|---|---|---|---|---|---|
| qwen2.5:0.5b (el de hoy) | 4 | 0 | 0 | 0 | 38 s (2 sin respuesta en 60 s) |
| lfm3 | 3 | 2 | 4 | 0 | 0.7 s |
| gemma4:e4b-it-qat | 6 | 2 | 1 | 0 | 2.2 s |
| **nemotron-3.5-bolt:e4b** | 5 | 3 | 3 | 0 | 4.7 s |
| qwen3.5:9b | 7 | 3 | 2 | 0 | 10 s |
| gemma4:12b-it-qat | 7 | 4 | 1 | 1 | 9.8 s |

(Medido antes de mejorar la lectura de las respuestas, abajo: `qwen3.5:9b` habría dicho NINGUNA bien en 3 de 4.)

- `qwen2.5:0.5b` es el peor: respuestas confusas y lentas. Hay que cambiarlo.
- `lfm3` es muy conservador: casi siempre dice NINGUNA (5 de 8 casos claros), aunque nunca inventa.
- `gemma4:12b` y `qwen3.5:9b` escriben mejor, pero son lentos y de los que más inventan cuando no hay nada que concluir.
- `nemotron-3.5-bolt:e4b` es el mejor equilibrio: frases cortas y limpias, reconoce cuándo no hay conclusión y no dejó pasar ninguna inventada.

**Conclusión:** para las conclusiones, usar `STONEWAVE_CONCLUSION_MODEL=nemotron-3.5-bolt:e4b`.

## Mejoras que salieron de esta prueba

- Algunos modelos contestan "No existe ninguna conclusión clara…" o "Ningún recuerdo…" en vez de `NINGUNA`, o empiezan con "NUEVA CONCLUSIÓN:".
  El motor ahora lo reconoce (sin esto se guardaban como conclusión).
