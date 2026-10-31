import os
import sqlite3
import json
import requests
import math
import time
from pathlib import Path

# --- CONFIGURACIÓN DINÁMICA DE RUTAS ---
USER_HOME = Path.home()
DEFAULT_MEM_FOLDER = USER_HOME / ".memholografic"
DEFAULT_DB_PATH = str(DEFAULT_MEM_FOLDER / "memoria.db")

OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_SYSTEMONE_URL = "http://localhost:11434/v1/systemone"
EMBEDDING_MODEL = os.getenv("MEMHOLOGRAFIC_EMBED_MODEL", "embeddinggemma-2:270m")
LLM_MODEL = os.getenv("MEMHOLOGRAFIC_LLM_MODEL", "tev1:0.8b")
SYNTHESIS_MODEL = os.getenv("MEMHOLOGRAFIC_SYNTHESIS_MODEL", "qwen2.5:0.5b")

# Si la mejor coincidencia de la biblioteca es menor a esto, tambien se busca en el sotano
WEAK_MATCH = 0.65

CLASSIFICATION_TOPICS = [
    "identidad", "proyecto", "trabajo", "estudios", "tecnologia",
    "fisica", "mecanica_cuantica", "astrofisica", "ciberseguridad",
    "videojuegos", "preferencias", "otro"
]

class MemHolograficEngine:
    def __init__(self, db_path=None):
        self.db_path = db_path if db_path else os.getenv("MEMHOLOGRAFIC_DB", DEFAULT_DB_PATH)
        self._ensure_db_folder()
        self._init_db()

    def _ensure_db_folder(self):
        folder = Path(self.db_path).parent
        folder.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, last_used REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS archived_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_name TEXT UNIQUE NOT NULL, description TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS fact_topics (fact_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, FOREIGN KEY (fact_id) REFERENCES facts(id), FOREIGN KEY (topic_id) REFERENCES topics(id), PRIMARY KEY (fact_id, topic_id))")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_namespace ON facts(namespace)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_topics_topic ON fact_topics(topic_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_topics_fact ON fact_topics(fact_id)")
            cols = [c[1] for c in cursor.execute("PRAGMA table_info(facts)").fetchall()]
            if "last_used" not in cols:
                cursor.execute("ALTER TABLE facts ADD COLUMN last_used REAL")
                cursor.execute("UPDATE facts SET last_used=?", (time.time(),))
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
                cursor.execute("DELETE FROM facts WHERE namespace=?", (namespace,))
                cursor.execute("DELETE FROM archived_facts WHERE namespace=?", (namespace,))
            else:
                cursor.execute("DELETE FROM facts")
                cursor.execute("DELETE FROM archived_facts")
            conn.commit()

    def get_embedding(self, text, retries=3):
        for attempt in range(retries):
            try:
                r = requests.post(OLLAMA_URL, json={"model": EMBEDDING_MODEL, "prompt": text}, timeout=10)
                if r.status_code == 200:
                    emb = r.json().get("embedding")
                    if emb: return emb
                r = requests.post("http://localhost:11434/api/embed", json={"model": EMBEDDING_MODEL, "input": text}, timeout=10)
                if r.status_code == 200:
                    res = r.json()
                    emb_list = res.get("embeddings")
                    if isinstance(emb_list, list) and len(emb_list) > 0:
                        return emb_list[0]
                    return res.get("embedding")
            except Exception:
                time.sleep(1)
        return None

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
                topic_id = row[0]
                cursor.execute("INSERT OR IGNORE INTO fact_topics (fact_id, topic_id) VALUES (?, ?)", (fact_id, topic_id))

    def calc_sim(self, v1, v2):
        if not v1 or not v2: return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        m1 = math.sqrt(sum(a * a for a in v1))
        m2 = math.sqrt(sum(a * a for a in v2))
        return dot / (m1 * m2) if m1 * m2 != 0 else 0.0

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

    def _ask_llm_synthesis(self, t1, t2):
        prompt = f"Eres un sintetizador de conocimiento. Tienes dos frases que dicen cosas similares. Crea una sola frase consolidada que mantenga la esencia de ambas, eliminando redundancias y siendo preciso. \n\nFrase 1: {t1}\nFrase 2: {t2}\n\nRespuesta (solo la frase sintetizada):"
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": SYNTHESIS_MODEL, "prompt": prompt, "stream": False, "think": False}, timeout=30)
            if r.status_code == 200:
                return r.json().get("response", "").strip()
        except:
            pass
        return t1 if t2 in t1 else (t2 if t1 in t2 else f"{t1}. {t2}")

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

    def merge_waves(self, t1, t2):
        return self._ask_llm_synthesis(t1, t2)

    def inject(self, content, energy=0.5, namespace="default", pinned=False):
        vec = self.get_embedding(content)
        if vec is None:
            return "❌ ERROR: No se obtuvo embedding."
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, content, embedding FROM facts WHERE namespace=?", (namespace,))
            existing = cursor.fetchall()
            best_collision_id = None
            max_sim = 0.0
            for fid, fcontent, fvec_blob in existing:
                try:
                    fvec = json.loads(fvec_blob)
                    sim = self.calc_sim(vec, fvec)
                    if sim > 0.8 and sim > max_sim:
                        max_sim = sim
                        best_collision_id = fid
                except: continue
            
            if best_collision_id:
                cursor.execute("SELECT energy, content FROM facts WHERE id=?", (best_collision_id,))
                res = cursor.fetchone()
                if res:
                    energy_old = float(res[0]) if res[0] is not None else 0.5
                    old_content = res[1]
                    decision = self._ask_llm_conflict(old_content, content)
                    if "CONTRADICTORIO" in decision:
                        cursor.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used) VALUES (?, ?, ?, ?, ?)", (namespace, content, energy, json.dumps(vec), time.time()))
                        fact_id = cursor.lastrowid
                        if pinned:
                            cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (fact_id,))
                        topics = self.classify_topics(content)
                        self._store_topics(cursor, fact_id, topics)
                        conn.commit()
                        return f"🚫 CONTRADICCIÓN detectada. Guardado como dato independiente."
                    
                    new_energy = min(1.0, energy_old + 0.2)
                    new_content = self.merge_waves(old_content, content)
                    new_vec = self.get_embedding(new_content)
                    final_vec_blob = json.dumps(new_vec if new_vec else vec)
                    cursor.execute("UPDATE facts SET content=?, energy=?, embedding=?, last_used=? WHERE id=?", (new_content, new_energy, final_vec_blob, time.time(), best_collision_id))
                    cursor.execute("DELETE FROM fact_topics WHERE fact_id=?", (best_collision_id,))
                    new_topics = self.classify_topics(new_content)
                    self._store_topics(cursor, best_collision_id, new_topics)
                    if pinned:
                        cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (best_collision_id,))
                    conn.commit()
                    if new_topics:
                        return f"🌊 COLISION con ID {best_collision_id}! Sim: {max_sim:.2f} Energy: {new_energy:.2f} Temas: {', '.join(new_topics)}"
                    return f"🌊 COLISION con ID {best_collision_id}! Sim: {max_sim:.2f} Energy: {new_energy:.2f}"
            else:
                cursor.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used) VALUES (?, ?, ?, ?, ?)", (namespace, content, energy, json.dumps(vec), time.time()))
                fact_id = cursor.lastrowid
                if pinned:
                    cursor.execute("UPDATE facts SET pinned=1 WHERE id=?", (fact_id,))
                topics = self.classify_topics(content)
                self._store_topics(cursor, fact_id, topics)
                conn.commit()
                if topics:
                    return f"📍 NUEVA ONDA. Temas: {', '.join(topics)}"
                return "📍 NUEVA ONDA."

    def query(self, text, namespace="default"):
        vec = self.get_embedding(text)
        if vec is None: return "Error: No embedding"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query_topics = self.classify_topics(text)
            topic_filtered_ids = None
            if query_topics:
                placeholders = ",".join(["?"] * len(query_topics))
                cursor.execute(
                    f"SELECT DISTINCT ft.fact_id FROM fact_topics ft "
                    f"JOIN topics t ON ft.topic_id = t.id "
                    f"WHERE t.topic_name IN ({placeholders})",
                    query_topics
                )
                topic_filtered_ids = {row[0] for row in cursor.fetchall()}
            
            cursor.execute("SELECT id, energy, content, embedding FROM facts WHERE namespace=?", (namespace,))
            active_facts = cursor.fetchall()
            results = []
            for fid, fenergy, fcontent, fvec_blob in active_facts:
                if topic_filtered_ids is not None and fid not in topic_filtered_ids:
                    continue
                try:
                    fvec = json.loads(fvec_blob)
                    sim = self.calc_sim(vec, fvec)
                    if sim > 0.5:
                        results.append((sim, fid, fenergy, fcontent))
                except: continue
            best_active = max((r[0] for r in results), default=0.0)
            if results and best_active >= WEAK_MATCH:
                results.sort(key=lambda x: x[0] * (0.5 + 0.5 * min(float(x[2] or 0), 1.0)), reverse=True)
                top = results[:3]
                now = time.time()
                for _s, fid, fenergy, _c in top:
                    cursor.execute("UPDATE facts SET energy=?, last_used=? WHERE id=?", (min(1.0, float(fenergy or 0) + 0.1), now, fid))
                conn.commit()
                return top
            
            cursor.execute("SELECT id, energy, content, embedding FROM archived_facts WHERE namespace=?", (namespace,))
            archived_facts = cursor.fetchall()
            archived_results = []
            for fid, fenergy, fcontent, fvec_blob in archived_facts:
                try:
                    fvec = json.loads(fvec_blob)
                    sim = self.calc_sim(vec, fvec)
                    if sim > 0.5:
                        archived_results.append((sim, fid, fenergy, fcontent))
                except: continue
            if archived_results and max(a[0] for a in archived_results) > best_active:
                archived_results.sort(key=lambda x: x[0], reverse=True)
                best_sim, best_id, best_energy, best_content = archived_results[0]
                cursor.execute("SELECT embedding FROM archived_facts WHERE id=?", (best_id,))
                emb = cursor.fetchone()[0]
                cursor.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used) VALUES (?, ?, ?, ?, ?)", (namespace, best_content, min(1.0, best_energy + 0.2), emb, time.time()))
                self._store_topics(cursor, cursor.lastrowid, self.classify_topics(best_content))
                cursor.execute("DELETE FROM archived_facts WHERE id=?", (best_id,))
                conn.commit()
                return [(best_sim, -1, best_energy + 0.2, best_content)]
            if results:
                results.sort(key=lambda x: x[0], reverse=True)
                return results[:3]
            return "No related memories found."

    def decay(self, namespace="default", rate=0.95, floor=0.15):
        """Baja la energia segun los dias sin uso. Lo que cae bajo 'floor' pasa al sotano."""
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
                    cursor.execute("INSERT INTO archived_facts (namespace, content, energy, embedding) VALUES (?, ?, ?, ?)", (namespace, content, new_energy, emb))
                    cursor.execute("DELETE FROM facts WHERE id=?", (fid,))
                    cursor.execute("DELETE FROM fact_topics WHERE fact_id=?", (fid,))
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

    def dream(self, namespace="default"):
        self.decay(namespace)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, content, embedding, energy FROM facts WHERE namespace=? AND COALESCE(pinned,0)=0", (namespace,))
            facts = cursor.fetchall()
            if len(facts) < 2: return "No hay suficientes datos para soñar."
            
            processed_ids = set()
            changes_made = 0
            for i in range(len(facts)):
                id1, content1, vec1_blob, energy1 = facts[i]
                if id1 in processed_ids: continue
                for j in range(i + 1, len(facts)):
                    id2, content2, vec2_blob, energy2 = facts[j]
                    if id2 in processed_ids: continue
                    vec1 = json.loads(vec1_blob)
                    vec2 = json.loads(vec2_blob)
                    sim = self.calc_sim(vec1, vec2)
                    if sim > 0.8:
                        decision = self._ask_llm_conflict(content1, content2)
                        if "CONTRADICTORIO" in decision:
                            cursor.execute("INSERT INTO archived_facts (namespace, content, energy, embedding) VALUES (?, ?, ?, ?)", (namespace, content1, energy1, vec1_blob))
                            cursor.execute("DELETE FROM facts WHERE id=?", (id1,))
                            processed_ids.add(id1); processed_ids.add(id2); changes_made += 1; break 
                        else:
                            merged_content = self.merge_waves(content1, content2)
                            new_vec = self.get_embedding(merged_content)
                            cursor.execute("UPDATE facts SET content=?, energy=?, embedding=? WHERE id=?", (merged_content, energy2 + 0.2, json.dumps(new_vec if new_vec else vec2), id2))
                            cursor.execute("DELETE FROM facts WHERE id=?", (id1,))
                            processed_ids.add(id1); processed_ids.add(id2); changes_made += 1; break
            conn.commit()
            return f"Sleeptime finalizado para {namespace}. Cambios realizados: {changes_made}"

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
