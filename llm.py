"""Capa de modelos de StoneWave: TODO lo que habla con un modelo vive aqui.

Tres piezas, cada una con su propio modelo (se cambian por separado):

  Embedder   convierte texto en vectores            (STONEWAVE_EMBED_MODEL)
  Generator  modelo generativo: escribe texto libre (STONEWAVE_SYNTHESIS_MODEL; las conclusiones usan STONEWAVE_CONCLUSION_MODEL)
  Decider    modelo de decision: elige UNA opcion   (STONEWAVE_LLM_MODEL; endpoint /v1/systemone de Ollama)

El motor (mem_engine.py) solo arma las preguntas y usa estas piezas; no hace peticiones HTTP.
Para usar otro backend (LM Studio, llama.cpp, una API en la nube...) basta con clases con los mismos metodos:

    StoneWaveEngine(embedder=MiEmbedder(), generator=MiGenerador(), decider=MiDecisor())

Regla de las tres piezas: nunca lanzan excepciones; si el modelo no responde devuelven None
(o '' en Decider.choose) y el motor sigue funcionando sin ese resultado.
"""
import os
import time

import requests


def env_var(name, default=None):
    """Lee STONEWAVE_<name>; si no existe, usa el nombre anterior MEMHOLOGRAFIC_<name>."""
    return os.getenv("STONEWAVE_" + name, os.getenv("MEMHOLOGRAFIC_" + name, default))


OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_SYSTEMONE_URL = "http://localhost:11434/v1/systemone"
EMBEDDING_MODEL = env_var("EMBED_MODEL", "embeddinggemma-2:270m")
LLM_MODEL = env_var("LLM_MODEL", "d1:3b")  # antes tev1:0.8b (d1 archiva de menos por error; ver tests/compare_llm.py)
SYNTHESIS_MODEL = env_var("SYNTHESIS_MODEL", "qwen2.5:0.5b")
CONCLUSION_MODEL = env_var("CONCLUSION_MODEL", SYNTHESIS_MODEL)  # modelo generativo que propone las conclusiones


def _default_prefixes(model):
    """Los modelos de embeddings funcionan mucho mejor si la pregunta y el documento llevan su prefijo."""
    m = model.lower()
    if m.startswith("embeddinggemma"):
        return "task: search result | query: ", "title: none | text: "
    if m.startswith("nomic-embed"):
        return "search_query: ", "search_document: "
    return "", ""


_QP, _DP = _default_prefixes(EMBEDDING_MODEL)
QUERY_PREFIX = env_var("QUERY_PREFIX", _QP)
DOC_PREFIX = env_var("DOC_PREFIX", _DP)


class Embedder:
    """Vectores de texto (Ollama). Un recuerdo es un DOCUMENTO; una pregunta (una piedra) es una CONSULTA: llevan prefijos distintos."""

    def __init__(self, model=None, query_prefix=None, doc_prefix=None):
        self.model = model or EMBEDDING_MODEL
        self.query_prefix = QUERY_PREFIX if query_prefix is None else query_prefix
        self.doc_prefix = DOC_PREFIX if doc_prefix is None else doc_prefix

    @property
    def scheme(self):
        """Huella del modelo y los prefijos: si cambia, hay que recalcular los vectores guardados."""
        return f"{self.model}|{self.query_prefix}|{self.doc_prefix}"

    def _one(self, text, retries=3):
        for _attempt in range(retries):
            try:
                r = requests.post(OLLAMA_URL, json={"model": self.model, "prompt": text}, timeout=10)
                if r.status_code == 200:
                    emb = r.json().get("embedding")
                    if emb:
                        return emb
                r = requests.post(OLLAMA_EMBED_URL, json={"model": self.model, "input": text}, timeout=10)
                if r.status_code == 200:
                    res = r.json()
                    emb_list = res.get("embeddings")
                    if isinstance(emb_list, list) and len(emb_list) > 0:
                        return emb_list[0]
                    return res.get("embedding")
            except Exception:
                time.sleep(1)
        return None

    def _batch(self, texts, timeout):
        try:
            r = requests.post(OLLAMA_EMBED_URL, json={"model": self.model, "input": texts}, timeout=timeout)
            if r.status_code == 200:
                embs = r.json().get("embeddings")
                if isinstance(embs, list) and len(embs) == len(texts) and all(embs):
                    return embs
        except Exception:
            pass
        return None

    def doc(self, text, retries=3):
        """Vector de UN documento (un recuerdo), o None."""
        return self._one(self.doc_prefix + text, retries)

    def queries(self, texts):
        """Vectores de varias CONSULTAS (las piedras) en una sola llamada. Si falla, una por una (None donde no haya)."""
        embs = self._batch([self.query_prefix + t for t in texts], 30)
        if embs is not None:
            return embs
        return [self._one(self.query_prefix + t) for t in texts]

    def docs(self, texts):
        """Vectores de varios documentos en lote (para recalcular la memoria). Sin reintentos largos: lista de None si falla."""
        embs = self._batch([self.doc_prefix + t for t in texts], 120)
        return embs if embs is not None else [None] * len(texts)


class Generator:
    """Modelo generativo (Ollama /api/generate): recibe un prompt y devuelve texto libre."""

    def __init__(self, model=None):
        self.model = model or SYNTHESIS_MODEL

    def generate(self, prompt, timeout=30, model=None):
        """Texto que responde el modelo (puede ser ''), o None si no responde. 'model' cambia el modelo solo en esta llamada."""
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": model or self.model, "prompt": prompt, "stream": False, "think": False},
                              timeout=timeout)
            if r.status_code == 200:
                return r.json().get("response", "") or ""
        except Exception:
            pass
        return None


class Decider:
    """Modelo de decision (Ollama /v1/systemone): no escribe texto, ELIGE una opcion. Los modelos tev1, d1 y d3 son de este tipo."""

    def __init__(self, model=None):
        self.model = model or LLM_MODEL

    def ask(self, state, name, question, timeout=30):
        """Respuesta del modelo a UNA pregunta ({'type': 'choice', 'instructions': ..., 'criteria': {...}}):
        un dict (con 'choice' y a veces 'picks'), o None si no responde."""
        payload = {"model": self.model, "state": state, "questions": {name: question}}
        try:
            r = requests.post(OLLAMA_SYSTEMONE_URL, json=payload, timeout=timeout)
            if r.status_code == 200:
                ans = r.json().get("answers", {}).get(name, {})
                return ans if isinstance(ans, dict) else {}
        except Exception:
            pass
        return None

    def choose(self, state, instructions, criteria, name="relacion", timeout=30):
        """Una de las claves de 'criteria' (la opcion que elige el modelo), o '' si no responde o no elige una valida."""
        ans = self.ask(state, name, {"type": "choice", "instructions": instructions, "criteria": criteria}, timeout)
        choice = (ans or {}).get("choice", "")
        return choice if isinstance(choice, str) and choice in criteria else ""
