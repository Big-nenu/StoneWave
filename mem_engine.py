import os
import re
import sys
import shutil
import sqlite3
import json
import requests
import math
import time
from contextlib import closing
from pathlib import Path

try:
    import numpy as np
except ImportError:  # sin numpy funciona igual, solo mas lento
    np = None

# --- CONFIGURACIÓN DINÁMICA DE RUTAS ---
USER_HOME = Path.home()
DEFAULT_MEM_FOLDER = USER_HOME / ".stonewave"
OLD_MEM_FOLDER = USER_HOME / ".memholografic"  # nombre anterior del proyecto


def env_var(name, default=None):
    """Lee STONEWAVE_<name>; si no existe, usa el nombre anterior MEMHOLOGRAFIC_<name>."""
    return os.getenv("STONEWAVE_" + name, os.getenv("MEMHOLOGRAFIC_" + name, default))


def _default_db_path():
    """~/.stonewave/memoria.db. Si solo existe la carpeta anterior, copia ahi la memoria (la vieja queda de respaldo)."""
    new = DEFAULT_MEM_FOLDER / "memoria.db"
    old = OLD_MEM_FOLDER / "memoria.db"
    if not new.exists() and old.exists():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new)
            print(f"StoneWave: memoria copiada de {old} a {new} (la anterior queda de respaldo)", file=sys.stderr)
        except Exception:
            return str(old)
    return str(new)

OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_SYSTEMONE_URL = "http://localhost:11434/v1/systemone"
EMBEDDING_MODEL = env_var("EMBED_MODEL", "embeddinggemma-2:270m")
LLM_MODEL = env_var("LLM_MODEL", "tev1:0.8b")
SYNTHESIS_MODEL = env_var("SYNTHESIS_MODEL", "qwen2.5:0.5b")


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
CENTER_PRIOR = 2  # con pocos recuerdos el centrado se suaviza (media * n / (n + 2))
CACHE_REBUILD_BELOW = 64  # con menos recuerdos que esto, la cache se reconstruye al guardar (la media cambia mucho)

# --- AJUSTES DE LA BÚSQUEDA POR ONDAS ---
# Los umbrales estan calibrados para vectores centrados (ver tests/real_test.py)
STONES = int(env_var("STONES", "5"))                 # piedras por consulta
STONES_LLM = env_var("STONES_LLM", "false").lower() == "true"  # ángulos con LLM
WEAK_MATCH = 0.40  # si la mejor coincidencia de la biblioteca es menor, tambien se busca en el sotano
MIN_SIM = 0.18  # similitud minima para devolver un recuerdo
WAVE_MIN_SIM = 0.15  # similitud minima para que una piedra cuente un recuerdo en su onda
WAVE_SIZE = 8          # recuerdos por onda
DUPLICATE_SIM = 0.90  # arriba de esto, inyectar solo refuerza el recuerdo existente (duplicados medidos: 0.94-0.97)
CONFLICT_SIM = 0.40  # arriba de esto, el sueño revisa si se contradicen (contradicciones medidas: 0.44-0.76)
RELATED_SIM = 0.30  # arriba de esto, el sueño enlaza los recuerdos (sin relacion: negativo)
DREAM_MAX_CHECKS = 30  # revisiones de contradiccion con LLM por sueño
ANGLE_WEIGHT = 0.5     # peso de las piedras-angulo frente a la pregunta completa

STOPWORDS = {
    "que", "como", "cual", "cuales", "quien", "donde", "cuando", "cuanto", "para", "por",
    "con", "sin", "sobre", "entre", "del", "los", "las", "una", "uno", "unos", "unas",
    "este", "esta", "estos", "estas", "ese", "esa", "eso", "esto", "mis", "tus", "sus",
    "muy", "mas", "pero", "porque", "tengo", "tiene", "hace", "hacer", "estoy", "eres",
    "soy", "fue", "fui", "ser", "hay", "algo", "todo", "todos", "acuerdas", "recuerdas",
    "dime", "sabes", "cuál", "qué", "cómo", "dónde", "quién",
}

CLASSIFICATION_TOPICS = [
    "identidad", "proyecto", "trabajo", "estudios", "tecnologia",
    "fisica", "mecanica_cuantica", "astrofisica", "ciberseguridad",
    "videojuegos", "preferencias", "otro"
]

class StoneWaveEngine:
    def __init__(self, db_path=None):
        self.db_path = db_path if db_path else (env_var("DB") or _default_db_path())
        self._ensure_db_folder()
        self._init_db()
        # Cache de vectores en memoria. Se invalida sola cuando la BD cambia (PRAGMA data_version).
        self._cache = {}
        self._built_n = {}
        self._dv = None
        self._vconn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._ensure_embedding_scheme()

    def close(self):
        try:
            self._vconn.close()
        except Exception:
            pass

    def _ensure_embedding_scheme(self):
        """Si los vectores guardados se hicieron con otro modelo o prefijos, los recalcula (con respaldo)."""
        scheme = f"{EMBEDDING_MODEL}|{QUERY_PREFIX}|{DOC_PREFIX}"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='embed_scheme'").fetchone()
            if row and row[0] == scheme:
                return
            n = conn.execute("SELECT (SELECT COUNT(*) FROM facts) + (SELECT COUNT(*) FROM archived_facts)").fetchone()[0]
            if n == 0:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_scheme', ?)", (scheme,))
                conn.commit()
                return
        bak = self.db_path + ".bak-embeddings"
        if not os.path.exists(bak):
            shutil.copy2(self.db_path, bak)
        print(f"StoneWave: recalculando los embeddings de {n} recuerdos (respaldo en {bak})", file=sys.stderr)
        updates = []
        for table in ("facts", "archived_facts"):
            with closing(sqlite3.connect(self.db_path)) as conn:
                rows = conn.execute(f"SELECT id, content FROM {table}").fetchall()
            for start in range(0, len(rows), 32):
                chunk = rows[start:start + 32]
                vecs = self.embed_docs([c for _, c in chunk])
                if any(v is None for v in vecs):
                    print("StoneWave: no se pudieron recalcular los embeddings (¿Ollama apagado?). Se reintentará.", file=sys.stderr)
                    return
                updates += [(table, fid, json.dumps(v)) for (fid, _), v in zip(chunk, vecs)]
        with sqlite3.connect(self.db_path) as conn:
            for table, fid, blob in updates:
                conn.execute(f"UPDATE {table} SET embedding=? WHERE id=?", (blob, fid))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_scheme', ?)", (scheme,))
            conn.commit()

    def _ensure_db_folder(self):
        folder = Path(self.db_path).parent
        folder.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, last_used REAL, pinned INTEGER DEFAULT 0)")
            cursor.execute("CREATE TABLE IF NOT EXISTS archived_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, reason TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_name TEXT UNIQUE NOT NULL, description TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS fact_topics (fact_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, FOREIGN KEY (fact_id) REFERENCES facts(id), FOREIGN KEY (topic_id) REFERENCES topics(id), PRIMARY KEY (fact_id, topic_id))")
            cursor.execute("CREATE TABLE IF NOT EXISTS links (fact_a INTEGER NOT NULL, fact_b INTEGER NOT NULL, sim REAL, kind TEXT, PRIMARY KEY (fact_a, fact_b))")
            cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_namespace ON facts(namespace)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_topics_topic ON fact_topics(topic_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_topics_fact ON fact_topics(fact_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_links_b ON links(fact_b)")
            cols = [c[1] for c in cursor.execute("PRAGMA table_info(facts)").fetchall()]
            if "last_used" not in cols:
                cursor.execute("ALTER TABLE facts ADD COLUMN last_used REAL")
                cursor.execute("UPDATE facts SET last_used=?", (time.time(),))
            arch_cols = [c[1] for c in cursor.execute("PRAGMA table_info(archived_facts)").fetchall()]
            if "reason" not in arch_cols:
                cursor.execute("ALTER TABLE archived_facts ADD COLUMN reason TEXT")
            if "pinned" not in cols:
                cursor.execute("ALTER TABLE facts ADD COLUMN pinned INTEGER DEFAULT 0")
            for topic in CLASSIFICATION_TOPICS:
                cursor.execute("INSERT OR IGNORE INTO topics (topic_name, description) VALUES (?, ?)", (topic, f"Tema: {topic}"))
            cursor.execute("CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE NOT NULL, tipo TEXT, embedding TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS relationships (id INTEGER PRIMARY KEY AUTOINCREMENT, entidad_origen TEXT NOT NULL, entidad_destino TEXT NOT NULL, tipo_relacion TEXT NOT NULL, hecho_origen_id INTEGER, peso REAL DEFAULT 1.0, FOREIGN KEY (hecho_origen_id) REFERENCES facts(id))")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_nombre ON entities(nombre)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_origen ON relationships(entidad_origen)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_destino ON relationships(entidad_destino)")
            conn.commit()

    def clear_db(self, namespace=None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if namespace:
                cursor.execute("DELETE FROM links WHERE fact_a IN (SELECT id FROM facts WHERE namespace=?) OR fact_b IN (SELECT id FROM facts WHERE namespace=?)", (namespace, namespace))
                cursor.execute("DELETE FROM facts WHERE namespace=?", (namespace,))
                cursor.execute("DELETE FROM archived_facts WHERE namespace=?", (namespace,))
            else:
                cursor.execute("DELETE FROM links")
                cursor.execute("DELETE FROM facts")
                cursor.execute("DELETE FROM archived_facts")
            conn.commit()

    def print_table(self, table="facts"):
        if table not in ("facts", "archived_facts"):
            raise ValueError("table debe ser 'facts' o 'archived_facts'")
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"SELECT id, energy, content FROM {table}").fetchall()
        print(f"--- {table} ({len(rows)}) ---")
        for r in rows:
            print(f"{r[0]:<5} | {float(r[1] or 0):<5.2f} | {r[2]}")

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    def _embed_raw(self, text, retries=3):
        for attempt in range(retries):
            try:
                r = requests.post(OLLAMA_URL, json={"model": EMBEDDING_MODEL, "prompt": text}, timeout=10)
                if r.status_code == 200:
                    emb = r.json().get("embedding")
                    if emb: return emb
                r = requests.post(OLLAMA_EMBED_URL, json={"model": EMBEDDING_MODEL, "input": text}, timeout=10)
                if r.status_code == 200:
                    res = r.json()
                    emb_list = res.get("embeddings")
                    if isinstance(emb_list, list) and len(emb_list) > 0:
                        return emb_list[0]
                    return res.get("embedding")
            except Exception:
                time.sleep(1)
        return None

    def get_embedding(self, text, retries=3):
        """Embedding de un DOCUMENTO (un recuerdo)."""
        return self._embed_raw(DOC_PREFIX + text, retries)

    def get_embeddings(self, texts):
        """Embeddings de PREGUNTAS (las piedras), en una sola llamada. Si falla, una por una."""
        try:
            r = requests.post(OLLAMA_EMBED_URL, json={"model": EMBEDDING_MODEL, "input": [QUERY_PREFIX + t for t in texts]}, timeout=30)
            if r.status_code == 200:
                embs = r.json().get("embeddings")
                if isinstance(embs, list) and len(embs) == len(texts) and all(embs):
                    return embs
        except Exception:
            pass
        return [self._embed_raw(QUERY_PREFIX + t) for t in texts]

    def embed_docs(self, texts):
        """Embeddings de documentos en lote (para recalcular la memoria). Sin reintentos largos."""
        try:
            r = requests.post(OLLAMA_EMBED_URL, json={"model": EMBEDDING_MODEL, "input": [DOC_PREFIX + t for t in texts]}, timeout=120)
            if r.status_code == 200:
                embs = r.json().get("embeddings")
                if isinstance(embs, list) and len(embs) == len(texts) and all(embs):
                    return embs
        except Exception:
            pass
        return [None] * len(texts)

    def calc_sim(self, v1, v2):
        if v1 is None or v2 is None or len(v1) == 0 or len(v2) == 0: return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        m1 = math.sqrt(sum(a * a for a in v1))
        m2 = math.sqrt(sum(a * a for a in v2))
        return dot / (m1 * m2) if m1 * m2 != 0 else 0.0

    # ------------------------------------------------------------------
    # Cache de vectores (rapidez): una matriz normalizada por namespace y tabla
    # ------------------------------------------------------------------
    def _check_version(self):
        dv = self._vconn.execute("PRAGMA data_version").fetchone()[0]
        if dv != self._dv:
            self._cache.clear()
            self._dv = dv

    def _sync_version(self):
        self._dv = self._vconn.execute("PRAGMA data_version").fetchone()[0]

    @staticmethod
    def _unit(m, mu):
        m = m - mu if mu is not None else m
        n = np.linalg.norm(m, axis=-1, keepdims=True)
        n[n == 0] = 1.0
        return m / n

    def _load_vectors(self, namespace, table="facts"):
        """Devuelve (ids, matriz, indice) de una tabla. Se guarda en cache hasta que la BD cambie."""
        self._check_version()
        key = (namespace, table)
        if key in self._cache:
            return self._cache[key]
        with closing(sqlite3.connect(self.db_path)) as conn:
            extra = " AND COALESCE(reason, '') != 'contradiccion'" if table == "archived_facts" else ""
            rows = conn.execute(f"SELECT id, embedding FROM {table} WHERE namespace=?{extra} ORDER BY id", (namespace,)).fetchall()
        ids, vecs, dim = [], [], None
        for fid, blob in rows:
            try:
                v = json.loads(blob)
            except Exception:
                continue
            if not v:
                continue
            if dim is None:
                dim = len(v)
            if len(v) != dim:
                continue
            ids.append(fid)
            vecs.append(v)
        mu = None
        if np is not None and ids:
            m = np.asarray(vecs, dtype=np.float32)
            # Centrar quita la parte "generica" que comparten todos los vectores y separa mucho mejor
            if table == "facts":
                mu = m.mean(axis=0) * (len(ids) / (len(ids) + CENTER_PRIOR))
            else:
                mu = self._load_vectors(namespace, "facts")[3]  # el sotano usa la media de la biblioteca
            mat = self._unit(m, mu)
        else:
            mat = vecs
        data = (ids, mat, {fid: n for n, fid in enumerate(ids)}, mu)
        self._cache[key] = data
        self._built_n[key] = len(ids)
        return data

    def _cache_add(self, namespace, fid, vec):
        """Agrega un recuerdo nuevo a la cache sin reconstruirla."""
        key = (namespace, "facts")
        entry = self._cache.get(key)
        if entry is not None:
            ids, mat, idx, mu = entry
            n_after = len(ids) + 1
            if n_after < CACHE_REBUILD_BELOW or n_after > 1.25 * self._built_n.get(key, 0):
                # la media usada para centrar ya no representa a la memoria: se reconstruye en la siguiente consulta
                del self._cache[key]
            elif np is not None and len(vec) == mat.shape[1]:
                v = self._unit(np.asarray(vec, dtype=np.float32), mu).reshape(1, -1)
                idx = dict(idx)
                idx[fid] = len(ids)
                self._cache[key] = (ids + [fid], np.vstack([mat, v]), idx, mu)
            elif np is None and len(vec) == len(mat[0]):
                idx = dict(idx)
                idx[fid] = len(ids)
                self._cache[key] = (ids + [fid], list(mat) + [vec], idx, mu)
        self._sync_version()

    def _sims(self, vec, namespace, table="facts"):
        ids, mat, _idx, mu = self._load_vectors(namespace, table)
        if not ids:
            return ids, []
        if np is not None:
            q = np.asarray(vec, dtype=np.float32)
            if q.shape[0] != mat.shape[1]:
                return ids, np.zeros(len(ids), dtype=np.float32)
            return ids, mat @ self._unit(q, mu)
        return ids, [self.calc_sim(vec, v) for v in mat]

    def _top(self, ids, sims, k, min_sim):
        if not ids:
            return []
        if np is not None:
            arr = np.asarray(sims)
            order = np.argsort(-arr)[:k]
            return [(ids[i], float(arr[i])) for i in order if arr[i] >= min_sim]
        pairs = sorted(zip(ids, sims), key=lambda p: p[1], reverse=True)[:k]
        return [(i, s) for i, s in pairs if s >= min_sim]

    def _cos(self, a, b):
        if np is not None:
            return float(a @ b)
        return self.calc_sim(a, b)

    def _similar_pairs(self, mat, min_sim):
        """Pares (i, j, sim) con i < j y sim >= min_sim."""
        n = len(mat)
        if np is not None:
            for start in range(0, n, 400):
                block = mat[start:start + 400] @ mat.T
                rows, cols = np.nonzero(block >= min_sim)
                for r, c in zip(rows, cols):
                    i = start + int(r)
                    j = int(c)
                    if j > i:
                        yield i, j, float(block[r, c])
        else:
            for i in range(n):
                for j in range(i + 1, n):
                    s = self.calc_sim(mat[i], mat[j])
                    if s >= min_sim:
                        yield i, j, s

    # ------------------------------------------------------------------
    # Modelos de Ollama
    # ------------------------------------------------------------------
    def classify_topics(self, text):
        try:
            criteria = {topic: f"El texto trata sobre {topic}" for topic in CLASSIFICATION_TOPICS}
            payload = {
                "model": LLM_MODEL,
                "state": text,
                "questions": {
                    "temas": {
                        "type": "choice",
                        "instructions": "Selecciona el tema que mejor se aplique a este texto.",
                        "criteria": criteria
                    }
                }
            }
            r = requests.post(OLLAMA_SYSTEMONE_URL, json=payload, timeout=30)
            if r.status_code == 200:
                data = r.json()
                answers = data.get("answers", {})
                temas_res = answers.get("temas", {})
                choice = temas_res.get("choice", "")
                if isinstance(choice, str) and choice in CLASSIFICATION_TOPICS:
                    return [choice]
                picks = temas_res.get("picks", [])
                if isinstance(picks, list):
                    return [p for p in picks if p in CLASSIFICATION_TOPICS]
        except Exception:
            pass
        return []

    def _store_topics(self, cursor, fact_id, topics):
        for topic_name in topics:
            cursor.execute("SELECT id FROM topics WHERE topic_name=?", (topic_name,))
            row = cursor.fetchone()
            if row:
                cursor.execute("INSERT OR IGNORE INTO fact_topics (fact_id, topic_id) VALUES (?, ?)", (fact_id, row[0]))

    def _ask_llm_conflict(self, t1, t2):
        # tev1 es un modelo de "decision": solo funciona con /v1/systemone (no con /api/generate)
        try:
            payload = {
                "model": LLM_MODEL,
                "state": f"Frase 1: {t1}\nFrase 2: {t2}",
                "questions": {
                    "relacion": {
                        "type": "choice",
                        "instructions": "Decide si la Frase 2 anula o contradice a la Frase 1, o si ambas pueden ser ciertas a la vez.",
                        "criteria": {
                            "contradictorio": "La Frase 2 contradice o invalida la Frase 1",
                            "complementario": "Ambas frases pueden coexistir o se complementan"
                        }
                    }
                }
            }
            r = requests.post(OLLAMA_SYSTEMONE_URL, json=payload, timeout=30)
            if r.status_code == 200:
                choice = r.json().get("answers", {}).get("relacion", {}).get("choice", "")
                if choice == "contradictorio":
                    return "CONTRADICTORIO"
        except Exception:
            pass
        return "COMPLEMENTARIO"

    def _ask_llm_reflection(self, facts_list):
        facts_str = "\n".join([f"- {f}" for f in facts_list])
        prompt = (
            f"Eres la unidad de reflexión de un cerebro digital. Tienes acceso a los siguientes recuerdos "
            f"de alta energía que parecen pertenecer a temas distintos, pero que el sistema considera interesantes: \n\n{facts_str}\n\n"
            f"Tu tarea es analizar estos datos y generar una HIPÓTESIS o INTUICIÓN. No resumas los datos, "
            f"busca una conexión oculta, una analogía o una pregunta filosófica que una estos temas. "
            f"La respuesta debe ser una sola frase corta que empiece con 'Intuición: '."
        )
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": SYNTHESIS_MODEL, "prompt": prompt, "stream": False, "think": False}, timeout=30)
            if r.status_code == 200:
                return r.json().get("response", "").strip()
        except:
            pass
        return None

    # ------------------------------------------------------------------
    # Guardar
    # ------------------------------------------------------------------
    def inject(self, content, energy=0.5, namespace="default", pinned=False):
        """Guarda un recuerdo. Rapido: no usa LLM. El sueño se encarga de ordenar y enlazar."""
        vec = self.get_embedding(content)
        if vec is None:
            return "❌ ERROR: No se obtuvo embedding."
        ids, sims = self._sims(vec, namespace)
        dup = self._top(ids, sims, 1, DUPLICATE_SIM)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if dup:
                fid, sim = dup[0]
                cursor.execute("UPDATE facts SET energy=MIN(1.0, COALESCE(energy, 0.5) + 0.2), last_used=? WHERE id=?", (time.time(), fid))
                if pinned:
                    cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (fid,))
                conn.commit()
                return f"🌊 REFUERZO: ya existía un recuerdo casi igual (ID {fid}, Sim: {sim:.2f}). Subió su energía."
            cursor.execute(
                "INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned) VALUES (?, ?, ?, ?, ?, ?)",
                (namespace, content, energy, json.dumps(vec), time.time(), 1 if pinned else 0))
            fact_id = cursor.lastrowid
            conn.commit()
        self._cache_add(namespace, fact_id, vec)
        return "📍 NUEVA ONDA."

    # ------------------------------------------------------------------
    # Consultar: las piedras y las ondas
    # ------------------------------------------------------------------
    def _angles_keywords(self, text):
        words = re.findall(r"\w+", text.lower())
        words = [w for w in words if len(w) > 3 and w not in STOPWORDS]
        seen = []
        for w in sorted(words, key=len, reverse=True):
            if w not in seen:
                seen.append(w)
        return seen[:STONES - 1]

    def _angles_llm(self, text):
        prompt = (f"Pregunta: {text}\nEscribe {STONES - 1} búsquedas cortas (2 a 4 palabras) que cubran "
                  f"distintos ángulos de la pregunta. Una por línea, sin números ni explicaciones.")
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": SYNTHESIS_MODEL, "prompt": prompt, "stream": False, "think": False}, timeout=20)
            if r.status_code == 200:
                lines = [l.strip(" -*•0123456789.\t") for l in r.json().get("response", "").splitlines()]
                return [l for l in lines if l][:STONES - 1]
        except Exception:
            pass
        return []

    def _make_stones(self, text):
        """La pregunta es la primera piedra; las demás son ángulos de la pregunta."""
        stones = [text]
        angles = self._angles_llm(text) if STONES_LLM else []
        if not angles:
            angles = self._angles_keywords(text)
        for a in angles:
            if a and a.lower() != text.lower() and a not in stones:
                stones.append(a)
        return stones[:STONES]

    def _throw(self, vecs, namespace, table):
        """Cada piedra hace su onda: los recuerdos mas parecidos a su angulo."""
        waves = []
        for v in vecs:
            if v is None:
                waves.append([])
                continue
            ids, sims = self._sims(v, namespace, table)
            waves.append(self._top(ids, sims, WAVE_SIZE, WAVE_MIN_SIM))
        return waves

    def _neighbors(self, fact_ids, limit=4):
        """Cascada asociativa: recuerdos enlazados (por el sueño) a los mejores candidatos."""
        if not fact_ids:
            return []
        q = ",".join("?" * len(fact_ids))
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                f"SELECT fact_a, fact_b, sim FROM links WHERE kind != 'contradice' AND (fact_a IN ({q}) OR fact_b IN ({q}))",
                list(fact_ids) + list(fact_ids)).fetchall()
        out = []
        for a, b, s in rows:
            if a in fact_ids and b not in fact_ids:
                out.append((b, s, a))
            elif b in fact_ids and a not in fact_ids:
                out.append((a, s, b))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:limit]

    def _rank(self, waves, namespace, table, use_links=True):
        """Suma las crestas, descarta los valles y repite con los mejores hasta quedar con pocos."""
        # Ronda 1: las crestas se suman (un recuerdo que sale en varias ondas pesa mas)
        score, best = {}, {}
        for n_wave, wave in enumerate(waves):
            weight = 1.0 if n_wave == 0 else ANGLE_WEIGHT  # la pregunta pesa mas que sus angulos
            for fid, s in wave:
                score[fid] = score.get(fid, 0.0) + weight * s
                best[fid] = max(best.get(fid, 0.0), s)
        if not score:
            return []
        top_score = max(score.values())
        # Los valles (poco peso, nada que ver) se descartan
        cand = [f for f in sorted(score, key=score.get, reverse=True)
                if score[f] >= 0.4 * top_score and best[f] >= MIN_SIM][:6]
        if not cand:
            return []
        norm = {f: score[f] / top_score for f in cand}
        raw = {f: best[f] for f in cand}
        top3 = cand[:3]
        if use_links:
            for nid, lsim, parent in self._neighbors(set(top3)):
                if nid not in norm:
                    norm[nid] = 0.5 * norm[parent] * lsim
                    raw[nid] = lsim
        # Ronda 2: los 3 mejores se vuelven las nuevas piedras; gana quien mas se parece a ellos
        ids, mat, idx, _mu = self._load_vectors(namespace, table)
        final = {}
        for f, n in norm.items():
            others = [t for t in top3 if t != f and t in idx]
            support = 0.0
            if others and f in idx:
                support = sum(self._cos(mat[idx[f]], mat[idx[t]]) for t in others) / len(others)
            final[f] = n + 0.5 * max(support, 0.0)
        q = ",".join("?" * len(final))
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = {r[0]: (r[1], r[2]) for r in conn.execute(
                f"SELECT id, energy, content FROM {table} WHERE id IN ({q})", list(final))}
        ranked = []
        for f, sc in final.items():
            if f not in rows:
                continue
            energy, content = rows[f]
            e = min(float(energy or 0), 1.0)
            ranked.append((sc * (0.5 + 0.5 * e), raw[f], f, e, content))
        ranked.sort(reverse=True)
        return [(r[1], r[2], r[3], r[4]) for r in ranked[:3]]

    def _touch(self, results):
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            for _s, fid, fenergy, _c in results:
                conn.execute("UPDATE facts SET energy=?, last_used=? WHERE id=?", (min(1.0, float(fenergy or 0) + 0.1), now, fid))
            conn.commit()
        self._sync_version()  # solo cambio energia, los vectores siguen validos

    def query(self, text, namespace="default"):
        stones = self._make_stones(text)
        vecs = self.get_embeddings(stones)
        if not vecs or vecs[0] is None:
            return "Error: No embedding"
        waves = self._throw(vecs, namespace, "facts")
        active = self._rank(waves, namespace, "facts")
        best_active = waves[0][0][1] if waves[0] else 0.0
        if active and best_active >= WEAK_MATCH:
            self._touch(active)
            return active

        # Coincidencia debil o nula: el Bibliotecario baja al sotano y lanza las piedras alla
        arch_waves = self._throw(vecs, namespace, "archived_facts")
        arch = self._rank(arch_waves, namespace, "archived_facts", use_links=False)
        if arch and arch[0][0] > best_active:
            sim, aid, aenergy, content = arch[0]
            new_energy = min(1.0, float(aenergy or 0.5) + 0.2)
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                emb = cur.execute("SELECT embedding FROM archived_facts WHERE id=?", (aid,)).fetchone()[0]
                cur.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned) VALUES (?, ?, ?, ?, ?, 0)",
                            (namespace, content, new_energy, emb, time.time()))
                new_id = cur.lastrowid
                cur.execute("DELETE FROM archived_facts WHERE id=?", (aid,))
                conn.commit()
            return [(sim, new_id, new_energy, content)]
        if active:
            return active
        return "No related memories found."

    # ------------------------------------------------------------------
    # Olvido, sueño y reflexión
    # ------------------------------------------------------------------
    def decay(self, namespace="default", rate=0.95, floor=0.15):
        """Baja la energia segun los dias sin uso. Lo que cae bajo 'floor' pasa al sotano. Los fijos no."""
        now = time.time()
        archived = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, content, energy, embedding, last_used FROM facts WHERE namespace=? AND COALESCE(pinned,0)=0", (namespace,))
            for fid, content, energy, emb, last_used in cursor.fetchall():
                days = max(0.0, (now - (last_used or now)) / 86400)
                if days < 0.05:
                    continue
                new_energy = float(energy if energy is not None else 0.5) * (rate ** days)
                if new_energy < floor:
                    cursor.execute("INSERT INTO archived_facts (namespace, content, energy, embedding, reason) VALUES (?, ?, ?, ?, 'desuso')", (namespace, content, new_energy, emb))
                    cursor.execute("DELETE FROM facts WHERE id=?", (fid,))
                    cursor.execute("DELETE FROM fact_topics WHERE fact_id=?", (fid,))
                    cursor.execute("DELETE FROM links WHERE fact_a=? OR fact_b=?", (fid, fid))
                    archived += 1
                else:
                    cursor.execute("UPDATE facts SET energy=?, last_used=? WHERE id=?", (new_energy, now, fid))
            conn.commit()
        return archived

    def get_all_facts(self, namespace="default"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM facts WHERE namespace=? ORDER BY COALESCE(pinned,0) DESC, energy DESC", (namespace,))
            return [r[0] for r in cursor.fetchall()]

    def _classify_pending(self, namespace, limit=50):
        """El sueño clasifica por tema los recuerdos que aun no tienen tema."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            pending = conn.execute(
                "SELECT id, content FROM facts WHERE namespace=? AND id NOT IN (SELECT fact_id FROM fact_topics) LIMIT ?",
                (namespace, limit)).fetchall()
        done = 0
        for fid, content in pending:
            topics = self.classify_topics(content)
            if not topics:
                break  # si el modelo no responde, no insistir con todos
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                self._store_topics(cur, fid, topics)
                conn.commit()
            done += 1
        return done

    def _link_facts(self, namespace):
        """Enlaza recuerdos parecidos SIN modificarlos. Si se contradicen, el mas viejo pasa al sotano."""
        ids, mat, _idx, _mu = self._load_vectors(namespace, "facts")
        if len(ids) < 2:
            return 0, 0, 0
        with closing(sqlite3.connect(self.db_path)) as conn:
            meta = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT id, content, COALESCE(pinned,0) FROM facts WHERE namespace=?", (namespace,))}
            known = {(r[0], r[1]): r[2] for r in conn.execute("SELECT fact_a, fact_b, kind FROM links")}
        pairs = []
        for i, j, s in self._similar_pairs(mat, RELATED_SIM):
            a, b = sorted((ids[i], ids[j]))
            pairs.append((s, a, b))
        pairs.sort(reverse=True)

        links, archive, per_fact = {}, set(), {}
        checks = contradictions = 0
        for s, a, b in pairs:
            if a in archive or b in archive or a not in meta or b not in meta:
                continue
            kind = "relacionado"
            if s >= CONFLICT_SIM:
                prev = known.get((a, b))
                if prev in ("similar", "contradice"):
                    kind = prev
                elif checks < DREAM_MAX_CHECKS:
                    checks += 1
                    v1 = self._ask_llm_conflict(meta[a][0], meta[b][0])
                    v2 = self._ask_llm_conflict(meta[b][0], meta[a][0]) if "CONTRADICTORIO" in v1 else v1
                    # un modelo chico se equivoca: solo cuenta como contradiccion si lo dice en los dos ordenes
                    kind = "contradice" if ("CONTRADICTORIO" in v1 and "CONTRADICTORIO" in v2) else "similar"
            if kind == "contradice" and not meta[a][1] and not meta[b][1]:
                archive.add(a)  # gana el mas nuevo; el viejo no se borra, baja al sotano
                contradictions += 1
                continue
            if kind == "relacionado":
                if per_fact.get(a, 0) >= 5 or per_fact.get(b, 0) >= 5:
                    continue
                per_fact[a] = per_fact.get(a, 0) + 1
                per_fact[b] = per_fact.get(b, 0) + 1
            links[(a, b)] = (s, kind)

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            for fid in archive:
                row = cur.execute("SELECT namespace, content, energy, embedding FROM facts WHERE id=?", (fid,)).fetchone()
                if row:
                    cur.execute("INSERT INTO archived_facts (namespace, content, energy, embedding, reason) VALUES (?, ?, ?, ?, 'contradiccion')", row)
                    cur.execute("DELETE FROM facts WHERE id=?", (fid,))
                    cur.execute("DELETE FROM fact_topics WHERE fact_id=?", (fid,))
            cur.execute("DELETE FROM links WHERE fact_a IN (SELECT id FROM facts WHERE namespace=?) OR fact_b IN (SELECT id FROM facts WHERE namespace=?)", (namespace, namespace))
            cur.execute("DELETE FROM links WHERE fact_a NOT IN (SELECT id FROM facts) OR fact_b NOT IN (SELECT id FROM facts)")
            for (a, b), (s, kind) in links.items():
                if a in archive or b in archive:
                    continue
                cur.execute("INSERT OR REPLACE INTO links (fact_a, fact_b, sim, kind) VALUES (?, ?, ?, ?)", (a, b, s, kind))
            conn.commit()
        similar = sum(1 for v in links.values() if v[1] == "similar")
        related = sum(1 for v in links.values() if v[1] == "relacionado")
        return contradictions, similar, related

    def dream(self, namespace="default"):
        """Sueño: baja al sotano lo que no se usa, clasifica por temas y enlaza recuerdos. No reescribe nada."""
        archived = self.decay(namespace)
        topics = self._classify_pending(namespace)
        contradictions, similar, related = self._link_facts(namespace)
        return (f"Sleeptime finalizado para {namespace}. Al sotano por desuso: {archived}. "
                f"Al sotano por contradiccion: {contradictions}. Enlaces: {similar} similares, {related} relacionados. "
                f"Temas clasificados: {topics}.")

    def reflect(self, namespace="default"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content, embedding FROM facts WHERE namespace=? ORDER BY energy DESC LIMIT 20", (namespace,))
            candidates = cursor.fetchall()
            if len(candidates) < 2:
                return "No hay suficientes recuerdos fuertes para reflexionar."
            representatives = []
            rep_vecs = []
            first_content, first_vec_blob = candidates[0]
            first_vec = json.loads(first_vec_blob)
            representatives.append(first_content)
            rep_vecs.append(first_vec)
            for content, vec_blob in candidates[1:]:
                vec = json.loads(vec_blob)
                if all(self.calc_sim(vec, rv) < 0.6 for rv in rep_vecs):
                    representatives.append(content)
                    rep_vecs.append(vec)
                if len(representatives) >= 3: break
            if len(representatives) < 2:
                representatives = [c[0] for c in candidates[:3]]
            reflection = self._ask_llm_reflection(representatives)
            if reflection:
                self.inject(reflection, energy=0.3, namespace=namespace)
                return f"Reflexión autónoma inyectada en {namespace}: {reflection}"
            return "tev no pudo encontrar una conexión interesante esta vez."


MemHolograficEngine = StoneWaveEngine  # nombre anterior, por compatibilidad
