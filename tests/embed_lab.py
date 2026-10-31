"""Laboratorio de embeddings: compara modelos, prefijos y centrado con las mismas preguntas.
Ejecutar:  python tests/embed_lab.py
"""
import os
import sys
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_test import FACTS, QUERIES

URL = "http://localhost:11434/api/embed"

CONFIGS = [
    # (modelo, prefijo de pregunta, prefijo de documento)
    ("embeddinggemma-2:270m", "", ""),
    ("embeddinggemma-2:270m", "task: search result | query: ", "title: none | text: "),
    ("embeddinggemma-2:740m-q8_0", "", ""),
    ("embeddinggemma-2:740m-q8_0", "task: search result | query: ", "title: none | text: "),
    ("nomic-embed-text:latest", "", ""),
    ("nomic-embed-text:latest", "search_query: ", "search_document: "),
]


def embed(model, texts):
    r = requests.post(URL, json={"model": model, "input": texts}, timeout=120)
    r.raise_for_status()
    return np.asarray(r.json()["embeddings"], dtype=np.float32)


def norm(m):
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


def evaluate(model, qp, dp, center):
    D = embed(model, [dp + f for f in FACTS])
    Q = embed(model, [qp + q for q, _ in QUERIES])
    if center:
        mu = D.mean(axis=0)
        D, Q = D - mu, Q - mu
    S = norm(Q) @ norm(D).T
    hits, margins, no_answer = 0, [], None
    total = 0
    for qi, (q, ok) in enumerate(QUERIES):
        if not ok:
            no_answer = float(S[qi].max())
            continue
        total += 1
        good = [i for i, f in enumerate(FACTS) if any(k in f for k in ok)]
        bad = [i for i in range(len(FACTS)) if i not in good]
        g, b = S[qi, good].max(), S[qi, bad].max()
        hits += g > b
        margins.append(float(g - b))
    return hits, total, float(np.mean(margins)), float(np.min(margins)), no_answer


print(f"{'modelo':<28}{'prefijos':<10}{'centrado':<10}{'top-1':<8}{'margen medio':<14}{'peor margen':<13}{'sim sin respuesta'}")
for model, qp, dp in CONFIGS:
    for center in (False, True):
        try:
            h, t, mm, mn, na = evaluate(model, qp, dp, center)
            print(f"{model:<28}{'si' if qp else 'no':<10}{'si' if center else 'no':<10}{h}/{t:<6}{mm:<14.3f}{mn:<13.3f}{na:.2f}")
        except Exception as ex:
            print(f"{model:<28}{'si' if qp else 'no':<10}{'si' if center else 'no':<10}ERROR {type(ex).__name__}: {str(ex)[:60]}")
