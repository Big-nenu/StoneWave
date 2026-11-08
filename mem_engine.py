import os
import re
import sys
import shutil
import sqlite3
import json
import requests
import math
import time
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
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
LLM_MODEL = env_var("LLM_MODEL", "d1:3b")  # antes tev1:0.8b (d1 archiva de menos por error; ver tests/compare_llm.py)
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
RESULTS = int(env_var("RESULTS", "3"))               # maximo de recuerdos que devuelve una consulta
STONES = int(env_var("STONES", "5"))                 # piedras por consulta
STONES_LLM = env_var("STONES_LLM", "false").lower() == "true"  # ángulos con LLM
WEAK_MATCH = 0.40  # si la mejor coincidencia de la biblioteca es menor, tambien se busca en el sotano
MIN_SIM = 0.18  # similitud minima para devolver un recuerdo
WAVE_MIN_SIM = 0.15  # similitud minima para que una piedra cuente un recuerdo en su onda
WAVE_SIZE = 8          # recuerdos por onda
DUPLICATE_SIM = 0.90  # arriba de esto, inyectar solo refuerza el recuerdo existente (duplicados medidos: 0.94-0.97)
CONFLICT_SIM = float(env_var("CONFLICT_SIM", "0.40"))  # arriba de esto, el sueño revisa si se contradicen (contradicciones medidas: 0.44-0.76)
RELATED_SIM = 0.30  # arriba de esto, el sueño enlaza los recuerdos (sin relacion: negativo)
BRIDGE_MIN_SIM = 0.15  # similitud minima con CADA uno de los dos temas para que un recuerdo cuente como puente (sin calibrar con datos reales)
DREAM_MAX_CHECKS = 30  # revisiones de contradiccion con LLM por sueño
DREAM_EVERY_HOURS = float(env_var("DREAM_HOURS", "12"))  # cada cuantas horas conviene soñar
BACKUP_KEEP_DAYS = int(env_var("BACKUP_DAYS", "7"))  # dias de respaldos diarios que se conservan (0 = sin respaldos)
BACKUP_NAME = re.compile(r"^(?P<stem>.+)-(?P<day>\d{4}-\d{2}-\d{2})\.db$")
ANGLE_WEIGHT = 0.5     # peso de las piedras-angulo frente a la pregunta completa
CONCLUSIONS_ON = env_var("CONCLUSIONS", "true").lower() == "true"  # el sueño saca conclusiones (hipotesis) de los recuerdos
CONCLUSION_MODEL = env_var("CONCLUSION_MODEL", SYNTHESIS_MODEL)  # modelo generativo que propone las conclusiones
CONCLUSION_GROUPS = 8        # grupos de recuerdos que se razonan por sueño
CONCLUSION_GROUP_SIZE = 4    # recuerdos por grupo: el pendiente + sus vecinos mas parecidos
CONCLUSION_MAX_CHECKS = 20   # revisiones de respaldo (modelo de decision) por sueño al pesar las hipotesis
CONCLUSION_START_ENERGY = 0.3  # una hipotesis nace con poca energia; sube si nuevos recuerdos la respaldan
CONFIRM_SUPPORT = 2          # recuerdos nuevos que la respaldan para pasar de 'hipotesis' a 'confirmada'

SYNC_FILENAME = "stonewave-memoria.jsonl"  # nombre del archivo compartido de `sync`

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

    def backup(self, keep_days=None, today=None):
        """Respaldo diario de la base de datos en <carpeta de la BD>/backups/<nombre>-AAAA-MM-DD.db.

        Hace como maximo uno por dia (si ya existe el de hoy no hace nada) y usa la API de respaldo de SQLite,
        asi que la copia es consistente aunque otro proceso este escribiendo. Despues borra los respaldos con mas de
        'keep_days' dias (se conservan hoy y los 'keep_days - 1' dias anteriores). Solo toca archivos con el patron
        <nombre>-AAAA-MM-DD.db de esta misma BD. 'today' (datetime.date) existe para las pruebas.
        Devuelve un texto con lo que hizo, o None si no hubo nada que hacer (apagado, BD inexistente o ya respaldada hoy).
        """
        keep_days = BACKUP_KEEP_DAYS if keep_days is None else keep_days
        src_path = Path(self.db_path)
        if keep_days <= 0 or not src_path.exists():
            return None
        today = today or datetime.now().date()
        folder = src_path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{src_path.stem}-{today.isoformat()}.db"
        msg = None
        if not final.exists():
            tmp = folder / (final.name + ".tmp")
            try:
                with closing(sqlite3.connect(self.db_path)) as src, closing(sqlite3.connect(str(tmp))) as dst:
                    src.backup(dst)
                os.replace(tmp, final)
                msg = f"Respaldo creado: {final}"
            except Exception as ex:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return f"No se pudo crear el respaldo: {ex}"
        removed = 0
        for f in folder.iterdir():
            m = BACKUP_NAME.match(f.name)
            if not m or m.group("stem") != src_path.stem:
                continue
            try:
                day = datetime.strptime(m.group("day"), "%Y-%m-%d").date()
            except ValueError:
                continue
            if (today - day).days >= keep_days:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            msg = (msg + ". " if msg else "") + f"Respaldos viejos borrados: {removed}"
        return msg

    def _ensure_embedding_scheme(self):
        """Si los vectores guardados se hicieron con otro modelo o prefijos, los recalcula (con respaldo)."""
        scheme = f"{EMBEDDING_MODEL}|{QUERY_PREFIX}|{DOC_PREFIX}"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='embed_scheme'").fetchone()
            if row and row[0] == scheme:
                return
            n = conn.execute("SELECT (SELECT COUNT(*) FROM facts) + (SELECT COUNT(*) FROM archived_facts) + (SELECT COUNT(*) FROM trash) + (SELECT COUNT(*) FROM conclusions)").fetchone()[0]
            if n == 0:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_scheme', ?)", (scheme,))
                conn.commit()
                return
        bak = self.db_path + ".bak-embeddings"
        if not os.path.exists(bak):
            shutil.copy2(self.db_path, bak)
        print(f"StoneWave: recalculando los embeddings de {n} recuerdos (respaldo en {bak})", file=sys.stderr)
        updates = []
        for table in ("facts", "archived_facts", "trash", "conclusions"):
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
            cursor.execute("CREATE TABLE IF NOT EXISTS trash (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, reason TEXT, replaced_by INTEGER, deleted_at REAL)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trash_namespace ON trash(namespace)")
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
            if "created_at" not in cols:
                cursor.execute("ALTER TABLE facts ADD COLUMN created_at REAL")
                cursor.execute("UPDATE facts SET created_at=COALESCE(last_used, ?)", (time.time(),))
            if "source" not in cols:
                cursor.execute("ALTER TABLE facts ADD COLUMN source TEXT")
            if "reasoned" not in cols:  # 0 = pendiente de razonar (el sueño le saca conclusiones)
                cursor.execute("ALTER TABLE facts ADD COLUMN reasoned INTEGER DEFAULT 0")
            cursor.execute("CREATE TABLE IF NOT EXISTS conclusions (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, status TEXT DEFAULT 'hipotesis', support INTEGER DEFAULT 0, against INTEGER DEFAULT 0, created_at REAL, last_checked REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS conclusion_sources (conclusion_id INTEGER NOT NULL, fact_id INTEGER NOT NULL, PRIMARY KEY (conclusion_id, fact_id))")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conclusions_namespace ON conclusions(namespace)")
            for topic in CLASSIFICATION_TOPICS:
                cursor.execute("INSERT OR IGNORE INTO topics (topic_name, description) VALUES (?, ?)", (topic, f"Tema: {topic}"))
            conn.commit()

    def clear_db(self, namespace=None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if namespace:
                cursor.execute("DELETE FROM links WHERE fact_a IN (SELECT id FROM facts WHERE namespace=?) OR fact_b IN (SELECT id FROM facts WHERE namespace=?)", (namespace, namespace))
                cursor.execute("DELETE FROM conclusion_sources WHERE conclusion_id IN (SELECT id FROM conclusions WHERE namespace=?)", (namespace,))
                cursor.execute("DELETE FROM conclusions WHERE namespace=?", (namespace,))
                cursor.execute("DELETE FROM facts WHERE namespace=?", (namespace,))
                cursor.execute("DELETE FROM archived_facts WHERE namespace=?", (namespace,))
                cursor.execute("DELETE FROM trash WHERE namespace=?", (namespace,))
            else:
                cursor.execute("DELETE FROM links")
                cursor.execute("DELETE FROM conclusion_sources")
                cursor.execute("DELETE FROM conclusions")
                cursor.execute("DELETE FROM facts")
                cursor.execute("DELETE FROM archived_facts")
                cursor.execute("DELETE FROM trash")
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
        data = self._cache.get(key)
        if data is not None:
            return data
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
                self._cache.pop(key, None)
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
    def inject(self, content, energy=0.5, namespace="default", pinned=False, source=None, created_at=None, reinforce=True):
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
                if not reinforce:  # sync: lo que ya existe no se refuerza (si no, cada sync inflaria la energia)
                    if pinned:
                        cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (fid,))
                    conn.commit()
                    return f"= YA EXISTE (ID {fid}, Sim: {sim:.2f})."
                cursor.execute("UPDATE facts SET energy=MIN(1.0, COALESCE(energy, 0.5) + 0.2), last_used=? WHERE id=?", (time.time(), fid))
                if pinned:
                    cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (fid,))
                conn.commit()
                return f"🌊 REFUERZO: ya existía un recuerdo casi igual (ID {fid}, Sim: {sim:.2f}). Subió su energía."
            cursor.execute(
                "INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned, created_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (namespace, content, energy, json.dumps(vec), time.time(), 1 if pinned else 0,
                 created_at if created_at is not None else time.time(), source))
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
                if score[f] >= 0.4 * top_score and best[f] >= MIN_SIM][:max(6, RESULTS + 2)]
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
        return [(r[1], r[2], r[3], r[4]) for r in ranked[:RESULTS]]

    def _touch(self, results):
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            for _s, fid, fenergy, _c in results:
                conn.execute("UPDATE facts SET energy=?, last_used=? WHERE id=?", (min(1.0, float(fenergy or 0) + 0.1), now, fid))
            conn.commit()
        self._sync_version()  # solo cambio energia, los vectores siguen validos

    PAST_HINTS = ("antigu", "anterior", "antes", "pasad", "previ", "viej", "solia", "ya no")

    def _asks_past(self, text):
        """True si la pregunta pide algo de antes ('mi antigua jefa', 'mi bicicleta anterior'): ahi el sotano tambien cuenta."""
        t = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
        return any(h in t for h in self.PAST_HINTS)

    def query(self, text, namespace="default"):
        stones = self._make_stones(text)
        vecs = self.get_embeddings(stones)
        if not vecs or vecs[0] is None:
            return "Error: No embedding"
        waves = self._throw(vecs, namespace, "facts")
        active = self._rank(waves, namespace, "facts")
        best_active = waves[0][0][1] if waves[0] else 0.0
        past = self._asks_past(text)
        if active and best_active >= WEAK_MATCH and not past:  # si pide algo del pasado, tambien se mira el sotano
            self._touch(active)
            return active

        # Coincidencia debil o nula: el Bibliotecario baja al sotano y lanza las piedras alla
        arch_waves = self._throw(vecs, namespace, "archived_facts")
        arch = self._rank(arch_waves, namespace, "archived_facts", use_links=False)
        if arch and (arch[0][0] > best_active or past):
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

    # ------------------------------------------------------------------
    # Listar, editar, borrar, exportar e importar
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_text(text):
        """Para comparar textos sin importar mayusculas ni espacios de mas."""
        return " ".join(str(text).split()).casefold()

    @staticmethod
    def _iso(ts):
        return None if ts is None else datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_created_at(value):
        """Acepta fecha ISO 8601 o segundos (epoch). Si no se entiende, devuelve None (se usara la hora actual)."""
        if value is None or value == "" or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    def list_facts(self, namespace="default"):
        """Recuerdos activos como (id, contenido, energia, fijo): fijos primero y luego por energia."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, content, COALESCE(energy, 0), COALESCE(pinned, 0) FROM facts WHERE namespace=? "
                "ORDER BY COALESCE(pinned,0) DESC, energy DESC", (namespace,)).fetchall()
        return [(r[0], r[1], float(r[2]), bool(r[3])) for r in rows]

    def _facts_by_id(self, ids):
        """[(id, contenido)] de los IDs dados, en el mismo orden."""
        if not ids:
            return []
        with closing(sqlite3.connect(self.db_path)) as conn:
            found = dict(conn.execute(f"SELECT id, content FROM facts WHERE id IN ({','.join('?' * len(ids))})", list(ids)).fetchall())
        return [(i, found[i]) for i in ids if i in found]

    def _delete_fact(self, fid, content, namespace):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT energy, embedding FROM facts WHERE id=?", (fid,)).fetchone()
            if row:  # lo olvidado no se pierde del todo: pasa a la papelera (solo informa, no resucita)
                self._to_trash(conn, namespace, content, row[0], row[1], "olvidado")
            conn.execute("DELETE FROM facts WHERE id=?", (fid,))
            conn.execute("DELETE FROM fact_topics WHERE fact_id=?", (fid,))
            conn.execute("DELETE FROM links WHERE fact_a=? OR fact_b=?", (fid, fid))
            # copias del mismo texto en el sotano: si se quedaran, la consulta las resucitaria
            cur = conn.execute("DELETE FROM archived_facts WHERE namespace=? AND content=?", (namespace, content))
            removed = cur.rowcount
        return {"status": "deleted", "id": fid, "content": content, "where": "biblioteca", "archived_removed": removed}

    def forget(self, fact_id_or_text, namespace="default"):
        """Olvida UN recuerdo, por ID (numero) o por texto (igual, o casi igual por similitud).

        Devuelve un dict con 'status':
          'deleted'   -> se borro (trae 'id', 'content')
          'ambiguous' -> el texto coincide con varios: NO borra nada; trae 'candidates' [(id, contenido)] para elegir por ID
          'not_found' -> no hay coincidencia (puede traer 'suggestions' [(id, contenido)])
          'error'     -> entrada invalida o sin embeddings (trae 'message')
        Un texto solo de digitos se toma como ID.
        """
        key = fact_id_or_text
        if isinstance(key, str) and key.strip().isdigit():
            key = int(key.strip())
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            return {"status": "error", "message": "forget necesita un ID (numero) o el texto del recuerdo."}

        if isinstance(key, int):
            with closing(sqlite3.connect(self.db_path)) as conn:
                row = conn.execute("SELECT id, content FROM facts WHERE id=? AND namespace=?", (key, namespace)).fetchone()
            if row is None:
                return {"status": "not_found", "message": f"No hay un recuerdo activo con ID {key} en '{namespace}'."}
            return self._delete_fact(row[0], row[1], namespace)

        text = key.strip()
        if not text:
            return {"status": "error", "message": "Falta el ID o el texto del recuerdo."}
        wanted = self._norm_text(text)
        with closing(sqlite3.connect(self.db_path)) as conn:
            candidates = [r for r in conn.execute("SELECT id, content FROM facts WHERE namespace=?", (namespace,))
                          if self._norm_text(r[1]) == wanted]
            archived = [] if candidates else [r for r in conn.execute("SELECT id, content FROM archived_facts WHERE namespace=?", (namespace,))
                                              if self._norm_text(r[1]) == wanted]
        if not candidates and archived:  # solo existe en el sotano: tambien se puede olvidar
            with sqlite3.connect(self.db_path) as conn:
                a0 = conn.execute("SELECT energy, embedding FROM archived_facts WHERE id=?", (archived[0][0],)).fetchone()
                if a0:
                    self._to_trash(conn, namespace, archived[0][1], a0[0], a0[1], "olvidado")
                conn.executemany("DELETE FROM archived_facts WHERE id=?", [(a[0],) for a in archived])
            return {"status": "deleted", "id": None, "content": archived[0][1], "where": "sotano", "archived_removed": len(archived)}
        if not candidates:  # sin coincidencia exacta: por similitud (casi igual)
            vec = self.get_embedding(text)
            if vec is None:
                return {"status": "error", "message": "No se pudo buscar por similitud (sin embeddings). Usa el ID del recuerdo."}
            ids, sims = self._sims(vec, namespace)
            close_ids = [i for i, _s in self._top(ids, sims, 3, DUPLICATE_SIM)]
            if not close_ids:
                return {"status": "not_found", "message": "Ningun recuerdo es igual o casi igual a ese texto.",
                        "suggestions": self._facts_by_id([i for i, _s in self._top(ids, sims, 3, MIN_SIM)])}
            candidates = self._facts_by_id(close_ids)
        if len(candidates) > 1:
            return {"status": "ambiguous", "message": f"El texto coincide con {len(candidates)} recuerdos; no se borro nada. Elige uno por ID.",
                    "candidates": [(c[0], c[1]) for c in candidates]}
        return self._delete_fact(candidates[0][0], candidates[0][1], namespace)

    def update_memory(self, fact_id, new_content, namespace="default"):
        """Corrige el texto de un recuerdo por ID. Conserva su energia y si es fijo; recalcula su vector.

        Devuelve un dict con 'status': 'updated' (trae 'id', 'old', 'new' y 'similar_to' si ya hay otro casi igual),
        'not_found' o 'error'.
        """
        try:
            fid = int(fact_id)
        except (TypeError, ValueError):
            return {"status": "error", "message": "fact_id debe ser un numero."}
        new_content = (new_content or "").strip() if isinstance(new_content, str) else ""
        if not new_content:
            return {"status": "error", "message": "new_content no puede estar vacio."}
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT content FROM facts WHERE id=? AND namespace=?", (fid, namespace)).fetchone()
        if row is None:
            return {"status": "not_found", "message": f"No hay un recuerdo activo con ID {fid} en '{namespace}'."}
        vec = self.get_embedding(new_content)
        if vec is None:
            return {"status": "error", "message": "No se obtuvo embedding; el recuerdo no se modifico."}
        ids, sims = self._sims(vec, namespace)
        twins = [i for i, _s in self._top(ids, sims, 3, DUPLICATE_SIM) if i != fid]
        with sqlite3.connect(self.db_path) as conn:
            old = conn.execute("SELECT energy, embedding FROM facts WHERE id=?", (fid,)).fetchone()
            if old and self._norm_text(row[0]) != self._norm_text(new_content):  # el texto viejo queda en la papelera como 'reemplazado'
                self._to_trash(conn, namespace, row[0], old[0], old[1], "reemplazado", fid)
            conn.execute("UPDATE facts SET content=?, embedding=?, last_used=?, reasoned=0 WHERE id=?", (new_content, json.dumps(vec), time.time(), fid))
            conn.execute("DELETE FROM fact_topics WHERE fact_id=?", (fid,))  # el sueño lo vuelve a clasificar
            conn.execute("DELETE FROM links WHERE fact_a=? OR fact_b=?", (fid, fid))  # y a enlazar
        return {"status": "updated", "id": fid, "old": row[0], "new": new_content, "similar_to": twins[0] if twins else None}

    # ------------------------------------------------------------------
    # Papelera (trash): lo olvidado y lo reemplazado. Solo informa; nunca resucita sola.
    # ------------------------------------------------------------------
    def _to_trash(self, conn, namespace, content, energy, embedding, reason, replaced_by=None):
        """Guarda un recuerdo en la papelera usando la conexion dada. Si ya esta el mismo texto con el mismo motivo, solo renueva la fecha."""
        now = time.time()
        row = conn.execute("SELECT id FROM trash WHERE namespace=? AND content=? AND reason=?", (namespace, content, reason)).fetchone()
        if row:
            conn.execute("UPDATE trash SET deleted_at=?, replaced_by=? WHERE id=?", (now, replaced_by, row[0]))
        else:
            conn.execute("INSERT INTO trash (namespace, content, energy, embedding, reason, replaced_by, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (namespace, content, energy, embedding, reason, replaced_by, now))

    def query_trash(self, text, namespace="default", limit=3):
        """Busca en la papelera lo que se parezca a 'text'. No modifica nada ni resucita nada.

        Devuelve una lista de dicts: sim, id, content, reason ('olvidado' | 'reemplazado'), deleted_at (ISO) y
        current (el texto que lo reemplazo, si sigue activo; si no, None).
        """
        vecs = self.get_embeddings([text])
        if not vecs or vecs[0] is None:
            return []
        ids, sims = self._sims(vecs[0], namespace, "trash")
        top = self._top(ids, sims, limit, MIN_SIM)
        if not top:
            return []
        q = ",".join("?" * len(top))
        out = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = {r[0]: r[1:] for r in conn.execute(
                f"SELECT id, content, reason, replaced_by, deleted_at FROM trash WHERE id IN ({q})", [i for i, _s in top])}
            for tid, sim in top:
                if tid not in rows:
                    continue
                content, reason, replaced_by, deleted_at = rows[tid]
                current = None
                if replaced_by is not None:
                    r = conn.execute("SELECT content FROM facts WHERE id=? AND namespace=?", (replaced_by, namespace)).fetchone()
                    current = r[0] if r else None
                out.append({"sim": float(sim), "id": tid, "content": content, "reason": reason,
                            "deleted_at": self._iso(deleted_at), "deleted_ts": deleted_at, "current": current})
        return out

    def list_trash(self, namespace="default"):
        """Papelera como (id, contenido, motivo, fecha ISO), lo mas reciente primero."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute("SELECT id, content, reason, deleted_at FROM trash WHERE namespace=? ORDER BY deleted_at DESC, id DESC", (namespace,)).fetchall()
        return [(r[0], r[1], r[2], self._iso(r[3])) for r in rows]

    def purge_trash(self, namespace="default", trash_id=None):
        """Borra DE VERDAD de la papelera: uno por ID, o todo el namespace si trash_id es None. Devuelve cuantos borro."""
        with sqlite3.connect(self.db_path) as conn:
            if trash_id is None:
                cur = conn.execute("DELETE FROM trash WHERE namespace=?", (namespace,))
            else:
                cur = conn.execute("DELETE FROM trash WHERE namespace=? AND id=?", (namespace, int(trash_id)))
            return cur.rowcount

    def restore_from_trash(self, trash_id, namespace="default"):
        """Devuelve un recuerdo de la papelera a la biblioteca (solo a peticion del usuario).

        Devuelve un dict con 'status': 'restored' (trae 'id', 'content' y 'current': el valor nuevo que lo reemplazo, si sigue activo),
        'exists' (ya hay un recuerdo activo igual), 'not_found' o 'error'.
        """
        try:
            tid = int(trash_id)
        except (TypeError, ValueError):
            return {"status": "error", "message": "trash_id debe ser un numero."}
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT content, energy, embedding, replaced_by FROM trash WHERE id=? AND namespace=?", (tid, namespace)).fetchone()
            if row is None:
                return {"status": "not_found", "message": f"No hay un recuerdo con ID {tid} en la papelera de '{namespace}'."}
            content, energy, emb, replaced_by = row
            wanted = self._norm_text(content)
            if any(self._norm_text(r[0]) == wanted for r in conn.execute("SELECT content FROM facts WHERE namespace=?", (namespace,))):
                conn.execute("DELETE FROM trash WHERE id=?", (tid,))
                return {"status": "exists", "message": "Ya hay un recuerdo activo igual; no se duplicó."}
            current = None
            if replaced_by is not None:
                r = conn.execute("SELECT content FROM facts WHERE id=? AND namespace=?", (replaced_by, namespace)).fetchone()
                current = r[0] if r else None
            now = time.time()
            cur = conn.execute(
                "INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned, created_at, source) VALUES (?, ?, ?, ?, ?, 0, ?, 'restaurado')",
                (namespace, content, max(float(energy if energy is not None else 0.5), 0.5), emb, now, now))
            new_id = cur.lastrowid
            conn.execute("DELETE FROM trash WHERE id=?", (tid,))
        return {"status": "restored", "id": new_id, "content": content, "current": current}

    def export_memories(self, path, namespace=None):
        """Exporta los recuerdos activos a un .jsonl canonico (un objeto JSON por linea).

        Campos: content, namespace, fijo, energy, created_at (ISO 8601 UTC), source.
        namespace=None exporta todos los namespaces. El sotano no se exporta. Devuelve cuantos recuerdos escribio.
        """
        sql = "SELECT namespace, content, energy, pinned, COALESCE(created_at, last_used), source FROM facts"
        args = ()
        if namespace:
            sql += " WHERE namespace=?"
            args = (namespace,)
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(sql + " ORDER BY namespace, id", args).fetchall()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for ns, content, energy, pinned, ts, source in rows:
                f.write(json.dumps({
                    "content": content, "namespace": ns, "fijo": bool(pinned),
                    "energy": round(float(energy if energy is not None else 0.5), 3),
                    "created_at": self._iso(ts), "source": source or "stonewave",
                }, ensure_ascii=False) + "\n")
        return len(rows)

    def import_records(self, records, namespace=None, run_dream=True, default_source="import", reinforce=True):
        """Importa recuerdos (dicts con el formato canonico) llamando a inject() con cada uno.

        namespace: si se da, TODOS van a ese namespace; si no, se respeta el de cada registro (o 'default').
        Al terminar corre dream() en cada namespace donde entraron recuerdos nuevos (run_dream=True).
        Si Ollama no responde 3 veces seguidas, se detiene. Devuelve un dict resumen:
        new, reinforced, errors, aborted, invalid [(linea, motivo)], namespaces, dream [mensajes].
        """
        summary = {"new": 0, "reinforced": 0, "errors": 0, "aborted": False, "invalid": [], "namespaces": [], "dream": []}
        new_in, consecutive = [], 0
        for n, rec in enumerate(records, 1):
            where = rec.get("_line", n) if isinstance(rec, dict) else n
            if not isinstance(rec, dict):
                summary["invalid"].append((where, "no es un objeto JSON"))
                continue
            content = rec.get("content")
            if not isinstance(content, str) or not content.strip():
                summary["invalid"].append((where, "falta 'content' (texto del recuerdo)"))
                continue
            ns = namespace or rec.get("namespace") or "default"
            if not isinstance(ns, str) or not ns.strip() or len(ns) > 100:
                summary["invalid"].append((where, "'namespace' debe ser un texto de 1 a 100 caracteres"))
                continue
            try:
                energy = min(1.0, max(0.0, float(rec.get("energy", 0.5))))
            except (TypeError, ValueError):
                summary["invalid"].append((where, "'energy' debe ser un numero entre 0 y 1"))
                continue
            msg = self.inject(content.strip(), reinforce=reinforce, energy=energy, namespace=ns, pinned=bool(rec.get("fijo", False)),
                              source=str(rec.get("source") or default_source),
                              created_at=self._parse_created_at(rec.get("created_at")))
            if "ERROR" in msg:
                summary["errors"] += 1
                consecutive += 1
                if consecutive >= 3:
                    summary["aborted"] = True
                    break
                continue
            consecutive = 0
            if ns not in summary["namespaces"]:
                summary["namespaces"].append(ns)
            if "NUEVA" in msg:
                summary["new"] += 1
                if ns not in new_in:
                    new_in.append(ns)
            else:
                summary["reinforced"] += 1
        if run_dream:
            for ns in new_in:
                try:
                    summary["dream"].append(self.dream(namespace=ns))
                except Exception as ex:  # el sueño es un extra: no debe tirar la importacion
                    summary["dream"].append(f"El sueño de '{ns}' fallo: {ex}")
        return summary

    def import_memories(self, path, namespace=None, run_dream=True, reinforce=True):
        """Importa un .jsonl canonico (ver export_memories). Las lineas invalidas se reportan, no detienen la importacion."""
        records, invalid = [], []
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig tolera el BOM de Windows
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    invalid.append((ln, "JSON invalido"))
                    continue
                if not isinstance(rec, dict):
                    invalid.append((ln, "no es un objeto JSON"))
                    continue
                rec["_line"] = ln
                records.append(rec)
        summary = self.import_records(records, namespace=namespace, run_dream=run_dream, reinforce=reinforce)
        summary["invalid"] = sorted(invalid + summary["invalid"])
        return summary

    def sync_file(self, path):
        """Une esta memoria con un .jsonl compartido (por ejemplo en un repo privado) para usarla en varias compus.

        'path' es el archivo o una carpeta (ahi se usa SYNC_FILENAME). Hace dos pasos: 1) importa lo que haya en el archivo
        (lo que ya existe NO se refuerza, no se duplica y no se infla su energia) y 2) escribe el archivo con la union de todo.
        Si algo sale mal al importar (Ollama caido, lineas invalidas o marcas de conflicto de git) NO escribe el archivo,
        para no borrar los recuerdos de la otra compu.

        Devuelve un dict: status 'ok' (path, total, new, existing) o 'blocked' (message).
        """
        path = str(path)
        if os.path.isdir(path):
            path = os.path.join(path, SYNC_FILENAME)
        new = existing = 0
        if os.path.exists(path):
            s = self.import_memories(path, namespace=None, run_dream=True, reinforce=False)
            if s["invalid"]:
                ln, why = s["invalid"][0]
                return {"status": "blocked", "message": f"No se sincronizo: el archivo tiene {len(s['invalid'])} linea(s) invalida(s) "
                        f"(primera: {ln}, {why}). Si es un conflicto de git, resuelvelo y vuelve a correr sync."}
            if s["aborted"] or s["errors"]:
                return {"status": "blocked", "message": "No se sincronizo: Ollama no respondio al importar. Revisa con "
                        "'python mem_cli.py doctor' y vuelve a correr sync (el archivo no se toco)."}
            new, existing = s["new"], s["reinforced"]
        tmp = path + ".tmp"
        total = self.export_memories(tmp)
        with open(tmp, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f if l.strip()]
        recs.sort(key=lambda r: (r["namespace"], r.get("created_at") or "", r["content"]))  # orden estable: git fusiona mejor
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for r in recs:
                r["energy"] = round(float(r["energy"]), 1)  # menos cambios sueltos entre equipos
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        return {"status": "ok", "path": path, "total": total, "new": new, "existing": existing}

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
            # (contenido, fijo, fecha de creacion): la fecha decide quien gana en una contradiccion
            meta = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
                "SELECT id, content, COALESCE(pinned,0), COALESCE(created_at, last_used, 0) FROM facts WHERE namespace=?", (namespace,))}
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
                # gana el mas nuevo segun created_at (si empatan, el de ID mayor); el viejo no se borra, baja al sotano
                older = a if (meta[a][2], a) <= (meta[b][2], b) else b
                archive.add(older)
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

    # ------------------------------------------------------------------
    # Conclusiones (hipotesis): el sueño razona sobre los recuerdos nuevos
    # ------------------------------------------------------------------
    def _ask_llm_conclusion(self, facts_list):
        """Pide UNA conclusion a un modelo generativo. Devuelve el texto, '' si no hay conclusion clara, o None si el modelo no responde."""
        facts_str = "\n".join(f"- {f}" for f in facts_list)
        prompt = (
            "Estos son recuerdos guardados sobre una persona:\n\n" + facts_str + "\n\n"
            "Escribe UNA sola conclusión corta (una frase) que se desprenda de TODOS esos recuerdos juntos, por ejemplo un gusto, "
            "un hábito o una forma de trabajar o aprender. No repitas un recuerdo tal cual y no inventes nada que no esté respaldado. "
            "Si no hay una conclusión clara, responde exactamente: NINGUNA"
        )
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": CONCLUSION_MODEL, "prompt": prompt, "stream": False, "think": False}, timeout=60)
            if r.status_code != 200:
                return None
            text = r.json().get("response", "").strip()
        except Exception:
            return None
        line = next((l.strip(" -*•\t\"'") for l in text.splitlines() if l.strip()), "")
        if not line or line.upper().startswith("NINGUNA") or len(line) < 15:
            return ""
        return line[:240]

    def _ask_llm_support(self, fact, conclusion):
        """Modelo de decision: ¿el recuerdo respalda la conclusion? Devuelve 'RESPALDA', 'CONTRADICE' o 'NEUTRO' (si no responde, 'NEUTRO')."""
        try:
            payload = {
                "model": LLM_MODEL,
                "state": f"Recuerdo: {fact}\nConclusión: {conclusion}",
                "questions": {
                    "relacion": {
                        "type": "choice",
                        "instructions": "Decide si el Recuerdo respalda la Conclusión, la contradice, o no dice nada claro sobre ella.",
                        "criteria": {
                            "respalda": "El Recuerdo es evidencia a favor de la Conclusión",
                            "contradice": "El Recuerdo va en contra de la Conclusión",
                            "neutro": "El Recuerdo no dice nada claro sobre la Conclusión"
                        }
                    }
                }
            }
            r = requests.post(OLLAMA_SYSTEMONE_URL, json=payload, timeout=30)
            if r.status_code == 200:
                choice = r.json().get("answers", {}).get("relacion", {}).get("choice", "")
                if choice in ("respalda", "contradice"):
                    return choice.upper()
        except Exception:
            pass
        return "NEUTRO"

    def _conclusion_twin(self, vec, text, namespace):
        """ID de una conclusion ya guardada que sea igual o casi igual a esta, o None."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute("SELECT id, content, embedding FROM conclusions WHERE namespace=?", (namespace,)).fetchall()
        wanted = self._norm_text(text)
        for cid, content, _blob in rows:
            if self._norm_text(content) == wanted:
                return cid
        if np is None or not rows:
            return None
        _ids, _mat, _idx, mu = self._load_vectors(namespace, "facts")
        q = self._unit(np.asarray(vec, dtype=np.float32), mu)
        best, best_id = 0.0, None
        for cid, _content, blob in rows:
            try:
                v = np.asarray(json.loads(blob), dtype=np.float32)
            except Exception:
                continue
            if v.shape != q.shape:
                continue
            s = float(self._unit(v, mu) @ q)
            if s > best:
                best, best_id = s, cid
        return best_id if best >= DUPLICATE_SIM else None

    def _reason_pending(self, namespace, groups=None):
        """Razona los recuerdos pendientes (reasoned=0): agrupa cada uno con sus vecinos mas parecidos, un modelo generativo
        propone UNA conclusion y el modelo de decision comprueba que los recuerdos la respalden. Si pasa, queda guardada como
        'hipotesis' con poca energia. Devuelve (nuevas, reforzadas, descartadas).
        Si el modelo generativo no responde, el recuerdo sigue pendiente y se reintenta en el siguiente sueño."""
        groups = CONCLUSION_GROUPS if groups is None else groups
        with closing(sqlite3.connect(self.db_path)) as conn:
            pending = conn.execute(
                "SELECT id, embedding FROM facts WHERE namespace=? AND COALESCE(reasoned,0)=0 "
                "ORDER BY COALESCE(created_at, last_used, 0) LIMIT ?", (namespace, groups)).fetchall()
        new = reinforced = dropped = 0
        done, seen = [], set()
        for fid, blob in pending:
            try:
                vec = json.loads(blob)
            except Exception:
                done.append(fid)
                continue
            ids, sims = self._sims(vec, namespace)
            near = [i for i, _s in self._top(ids, sims, CONCLUSION_GROUP_SIZE, RELATED_SIM)]
            if fid not in near:
                near = [fid] + near[:CONCLUSION_GROUP_SIZE - 1]
            group = tuple(sorted(near))
            if len(group) < 2 or group in seen:  # sin vecinos no hay de donde concluir; si llegan vecinos, se agrupara con ellos
                done.append(fid)
                continue
            seen.add(group)
            facts = self._facts_by_id(list(group))
            texts = [c for _i, c in facts]
            concl = self._ask_llm_conclusion(texts)
            if concl is None:
                break  # el modelo no responde: no insistir; lo pendiente se reintenta en otro sueño
            if not concl:
                done.append(fid)
                continue
            cvec = self.get_embedding(concl)
            if cvec is None:
                break
            done.append(fid)
            verdicts = [self._ask_llm_support(t, concl) for t in texts]
            # un modelo chico inventa: la conclusion solo pasa si la mayoria de los recuerdos la respalda y ninguno la contradice
            if "CONTRADICE" in verdicts or verdicts.count("RESPALDA") < max(2, math.ceil(len(texts) / 2)):
                dropped += 1
                continue
            twin = self._conclusion_twin(cvec, concl, namespace)
            now = time.time()
            with sqlite3.connect(self.db_path) as conn:
                if twin is not None:
                    conn.execute("UPDATE conclusions SET energy=MIN(1.0, COALESCE(energy, 0.3) + 0.1), last_checked=? WHERE id=?", (now, twin))
                    cid = twin
                    reinforced += 1
                else:
                    cur = conn.execute(
                        "INSERT INTO conclusions (namespace, content, energy, embedding, status, support, against, created_at, last_checked) "
                        "VALUES (?, ?, ?, ?, 'hipotesis', 0, 0, ?, ?)",
                        (namespace, concl, CONCLUSION_START_ENERGY, json.dumps(cvec), now, now))
                    cid = cur.lastrowid
                    new += 1
                conn.executemany("INSERT OR IGNORE INTO conclusion_sources (conclusion_id, fact_id) VALUES (?, ?)", [(cid, i) for i, _c in facts])
                conn.commit()
        if done:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("UPDATE facts SET reasoned=1 WHERE id=?", [(i,) for i in done])
                conn.commit()
        self._sync_version()  # solo cambiaron tablas sin vectores de la biblioteca
        return new, reinforced, dropped

    def _weigh_conclusions(self, namespace):
        """Pesa las hipotesis con los recuerdos NUEVOS parecidos a ellas: los que las respaldan suben su energia, los que las
        contradicen la bajan. Con CONFIRM_SUPPORT respaldos pasa a 'confirmada'; si la energia llega a casi 0, se descarta.
        Una conclusion cuyos recuerdos de origen ya no existen tambien se descarta. Devuelve (confirmadas, descartadas)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM conclusion_sources WHERE fact_id NOT IN (SELECT id FROM facts)")
            orphans = [r[0] for r in conn.execute(
                "SELECT id FROM conclusions WHERE namespace=? AND id NOT IN (SELECT conclusion_id FROM conclusion_sources)", (namespace,))]
            conn.executemany("DELETE FROM conclusions WHERE id=?", [(i,) for i in orphans])
            conn.commit()
            rows = conn.execute(
                "SELECT id, content, embedding, COALESCE(energy, ?), status, COALESCE(support, 0), COALESCE(against, 0), "
                "COALESCE(last_checked, created_at, 0) FROM conclusions WHERE namespace=?", (CONCLUSION_START_ENERGY, namespace)).fetchall()
        dropped, confirmed, checks = len(orphans), 0, 0
        for cid, content, blob, energy, status, support, against, last in rows:
            if checks >= CONCLUSION_MAX_CHECKS:
                break
            try:
                vec = json.loads(blob)
            except Exception:
                continue
            ids, sims = self._sims(vec, namespace)
            with closing(sqlite3.connect(self.db_path)) as conn:
                srcs = {r[0] for r in conn.execute("SELECT fact_id FROM conclusion_sources WHERE conclusion_id=?", (cid,))}
                newer = {r[0] for r in conn.execute(
                    "SELECT id FROM facts WHERE namespace=? AND COALESCE(created_at, last_used, 0) > ?", (namespace, last))}
            candidates = [i for i, _s in self._top(ids, sims, 5, RELATED_SIM) if i in newer and i not in srcs]
            if not candidates:
                continue
            energy, complete, supporters = float(energy), True, []
            for fid, text in self._facts_by_id(candidates):
                if checks >= CONCLUSION_MAX_CHECKS:
                    complete = False
                    break
                checks += 1
                verdict = self._ask_llm_support(text, content)
                if verdict == "RESPALDA":
                    energy, support = min(1.0, energy + 0.15), support + 1
                    supporters.append(fid)
                elif verdict == "CONTRADICE":
                    energy, against = max(0.0, energy - 0.2), against + 1
            with sqlite3.connect(self.db_path) as conn:
                if energy <= 0.05:
                    conn.execute("DELETE FROM conclusion_sources WHERE conclusion_id=?", (cid,))
                    conn.execute("DELETE FROM conclusions WHERE id=?", (cid,))
                    dropped += 1
                else:
                    if status != "confirmada" and support >= CONFIRM_SUPPORT and energy >= 0.6 - 1e-9:
                        status = "confirmada"
                        confirmed += 1
                    elif status == "confirmada" and energy < 0.4:
                        status = "hipotesis"
                    conn.execute("UPDATE conclusions SET energy=?, status=?, support=?, against=?, last_checked=COALESCE(?, last_checked) WHERE id=?",
                                 (energy, status, support, against, time.time() if complete else None, cid))
                    conn.executemany("INSERT OR IGNORE INTO conclusion_sources (conclusion_id, fact_id) VALUES (?, ?)", [(cid, i) for i in supporters])
                conn.commit()
        self._sync_version()
        return confirmed, dropped

    def list_conclusions(self, namespace="default"):
        """Conclusiones como dicts: id, content, energy, status ('hipotesis' | 'confirmada'), support, against, sources [IDs].
        Las confirmadas primero y luego por energia."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, content, COALESCE(energy, 0), status, COALESCE(support, 0), COALESCE(against, 0) FROM conclusions "
                "WHERE namespace=? ORDER BY (status='confirmada') DESC, energy DESC, id DESC", (namespace,)).fetchall()
            srcs = {}
            for cid, fid in conn.execute("SELECT conclusion_id, fact_id FROM conclusion_sources"):
                srcs.setdefault(cid, []).append(fid)
        return [{"id": r[0], "content": r[1], "energy": float(r[2]), "status": r[3], "support": r[4], "against": r[5],
                 "sources": sorted(srcs.get(r[0], []))} for r in rows]

    def forget_conclusion(self, conclusion_id, namespace="default"):
        """Borra una conclusion (la que el sueño dedujo mal). No toca los recuerdos. Devuelve {'status': 'deleted'|'not_found'|'error', ...}."""
        try:
            cid = int(conclusion_id)
        except (TypeError, ValueError):
            return {"status": "error", "message": "conclusion_id debe ser un numero."}
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT content FROM conclusions WHERE id=? AND namespace=?", (cid, namespace)).fetchone()
            if row is None:
                return {"status": "not_found", "message": f"No hay una conclusion con ID {cid} en '{namespace}'."}
            conn.execute("DELETE FROM conclusion_sources WHERE conclusion_id=?", (cid,))
            conn.execute("DELETE FROM conclusions WHERE id=?", (cid,))
        return {"status": "deleted", "id": cid, "content": row[0]}

    def get_context(self, namespace="default", token_budget=600):
        """Resumen corto para arrancar una conversacion, con un limite de tokens (aprox. 4 caracteres por token):
        identidad (fijos), conclusiones y los recuerdos con mas energia. Cada recuerdo trae su [ID n] y cada conclusion su [C n]."""
        try:
            budget = int(token_budget)
        except (TypeError, ValueError):
            budget = 600
        budget = max(100, min(budget, 8000)) * 4
        facts = self.list_facts(namespace)
        concs = self.list_conclusions(namespace)
        if not facts and not concs:
            return "La memoria está vacía todavía."

        def clip(t):
            return t if len(t) <= 240 else t[:237] + "..."

        out, state = [f"Contexto ({len(facts)} recuerdos, {len(concs)} conclusiones):"], {"used": 0, "hidden": 0}
        state["used"] = len(out[0]) + 1

        def section(title, lines, cap):
            if not lines:
                return
            block, size, shown = [title], len(title) + 1, 0
            for ln in lines:
                if size + len(ln) + 1 > cap or state["used"] + size + len(ln) + 1 > budget:
                    break
                block.append(ln)
                size += len(ln) + 1
                shown += 1
            state["hidden"] += len(lines) - shown
            if shown:
                out.append("\n".join(block))
                state["used"] += size

        section("Identidad (fijos):", [f"- [ID {f[0]}] {clip(f[1])}" for f in facts if f[3]], int(budget * 0.45))
        section("Conclusiones (las marcadas como hipótesis aún no están confirmadas; no las trates como hechos):",
                [f"- [C {c['id']}] {clip(c['content'])} ({'confirmada' if c['status'] == 'confirmada' else 'hipótesis'})" for c in concs],
                int(budget * 0.25))
        section("Recuerdos con más energía:", [f"- [ID {f[0]}] {clip(f[1])}" for f in facts if not f[3]], budget - state["used"])
        if state["hidden"]:
            out.append(f"(+{state['hidden']} más sin mostrar; usa query_memory para buscar un tema)")
        return "\n".join(out)

    def dream(self, namespace="default"):
        """Sueño: baja al sotano lo que no se usa, clasifica por temas, enlaza recuerdos y razona (conclusiones). No reescribe ningun recuerdo."""
        archived = self.decay(namespace)
        topics = self._classify_pending(namespace)
        contradictions, similar, related = self._link_facts(namespace)
        msg = (f"Sleeptime finalizado para {namespace}. Al sotano por desuso: {archived}. "
                f"Al sotano por contradiccion: {contradictions}. Enlaces: {similar} similares, {related} relacionados. "
                f"Temas clasificados: {topics}.")
        if CONCLUSIONS_ON:  # razonar: el sueño pesa las hipotesis viejas y saca conclusiones de lo nuevo (nunca debe tirar el sueño)
            try:
                confirmed, w_drop = self._weigh_conclusions(namespace)
                c_new, c_re, c_drop = self._reason_pending(namespace)
                msg += f" Conclusiones: {c_new} nuevas, {c_re} reforzadas, {confirmed} confirmadas, {c_drop + w_drop} descartadas."
            except Exception as ex:
                print(f"StoneWave: error razonando conclusiones: {ex!r}", file=sys.stderr)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"last_dream:{namespace}", str(time.time())))
            conn.commit()
        return msg

    def hours_since_dream(self, namespace="default"):
        """Horas desde el ultimo sueño (manual o automatico). None si nunca se ha soñado."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (f"last_dream:{namespace}",)).fetchone()
        return None if row is None else (time.time() - float(row[0])) / 3600

    def dream_due(self, namespace="default", hours=None):
        """True si ya toca soñar: pasaron las horas indicadas, o nunca se ha soñado y hay recuerdos."""
        hours = DREAM_EVERY_HOURS if hours is None else hours
        h = self.hours_since_dream(namespace)
        if h is None:
            with closing(sqlite3.connect(self.db_path)) as conn:
                return conn.execute("SELECT COUNT(*) FROM facts WHERE namespace=?", (namespace,)).fetchone()[0] > 0
        return h >= hours

    def bridge(self, topic_a, topic_b, namespace="default", limit=3):
        """Puente semantico: recuerdos que se parecen a LOS DOS temas a la vez. No modifica nada.

        La puntuacion de un recuerdo es la MENOR de sus dos similitudes (un recuerdo que solo encaja con un tema no es puente).
        Devuelve un dict con 'status': 'found', 'none' (no hay puente) o 'error' (trae 'message'). Siempre trae 'a' y 'b'
        (lo mas cercano a cada tema, o None) y 'bridges': [{id, content, energy, sim_a, sim_b, score}].
        """
        if not all(isinstance(x, str) and x.strip() for x in (topic_a, topic_b)):
            return {"status": "error", "message": "bridge necesita dos temas (texto)."}
        vecs = self.get_embeddings([topic_a.strip(), topic_b.strip()])
        if not vecs or vecs[0] is None or vecs[1] is None:
            return {"status": "error", "message": "No se obtuvo embedding."}
        ids, sa = self._sims(vecs[0], namespace)
        if not ids:
            return {"status": "none", "bridges": [], "a": None, "b": None, "message": "La memoria esta vacia."}
        _ids, sb = self._sims(vecs[1], namespace)
        sa, sb = [float(x) for x in sa], [float(x) for x in sb]
        both = [min(x, y) for x, y in zip(sa, sb)]
        top = self._top(ids, both, limit, BRIDGE_MIN_SIM)
        best_a, best_b = self._top(ids, sa, 1, MIN_SIM), self._top(ids, sb, 1, MIN_SIM)
        wanted = {i for i, _s in top + best_a + best_b}
        rows = {}
        if wanted:
            with closing(sqlite3.connect(self.db_path)) as conn:
                rows = {r[0]: (r[1], float(r[2])) for r in conn.execute(
                    f"SELECT id, content, COALESCE(energy, 0) FROM facts WHERE id IN ({','.join('?' * len(wanted))})", list(wanted))}
        pos = {fid: n for n, fid in enumerate(ids)}

        def near(best):
            return {"id": best[0][0], "content": rows[best[0][0]][0], "sim": best[0][1]} if best and best[0][0] in rows else None

        bridges = [{"id": fid, "content": rows[fid][0], "energy": rows[fid][1], "sim_a": sa[pos[fid]], "sim_b": sb[pos[fid]], "score": s}
                   for fid, s in top if fid in rows]
        return {"status": "found" if bridges else "none", "bridges": bridges, "a": near(best_a), "b": near(best_b)}

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
