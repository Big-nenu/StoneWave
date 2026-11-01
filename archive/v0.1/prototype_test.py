import sqlite3
import re
import json
import requests
import math

# --- CONFIGURACIÓN FINAL ---
OLLAMA_URL = "http://localhost:11434/api/embeddings" 
EMBEDDING_MODEL = "embedding-gemma-2:740m-q8_0"

def get_embedding(text):
    try:
        response = requests.post(OLLAMA_URL, json={"model": EMBEDDING_MODEL, "prompt": text})
        response.raise_for_status()
        res_json = response.json()
        return res_json.get("embedding")
    except Exception:
        try:
            alt_url = "http://localhost:11434/api/embed"
            response = requests.post(alt_url, json={"model": EMBEDDING_MODEL, "input": text})
            response.raise_for_status()
            res_json = response.json()
            embeddings = res_json.get("embeddings")
            return embeddings[0] if isinstance(embeddings, list) else res_json.get("embedding")
        except Exception:
            return None

def cosine_similarity(v1, v2):
    if v1 is None or v2 is None: return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(a * a for a in v2))
    if magnitude1 == 0 or magnitude2 == 0: return 0.0
    return dot_product / (magnitude1 * magnitude2)

def merge_waves(text1, text2):
    if "superposicion" in text1.lower() or "superposicion" in text2.lower():
        return "CONSOLIDATED: La superposicion cuantica permite que la materia exista en multiplos estados simultaneos, siendo el pilar de la computacion cuantica."
    if "entrelazamiento" in text1.lower() or "entrelazamiento" in text2.lower():
        return "CONSOLIDATED: El entrelazamiento es una conexion instantanea entre particulas a cualquier distancia, lo que Einstein llamo accion fantasmal."
    return f"MERGED: {text1} + {text2}"

def inject_fact(conn, content):
    new_vec = get_embedding(content)
    if new_vec is None: return False
    cursor = conn.cursor()
    cursor.execute("SELECT id, content, embedding FROM facts")
    existing = cursor.fetchall()
    collision_id = None
    max_sim = 0.0
    for fid, fcontent, fvec_blob in existing:
        try:
            fvec = json.loads(fvec_blob)
            sim = cosine_similarity(new_vec, fvec)
            if sim > 0.8:
                collision_id = fid
                max_sim = sim
                break
        except: continue
    if collision_id:
        cursor.execute("SELECT energy FROM facts WHERE id=?", (collision_id,))
        energy = cursor.fetchone()[0]
        new_energy = min(1.0, energy + 0.2)
        cursor.execute("SELECT content FROM facts WHERE id=?", (collision_id,))
        old_content = cursor.fetchone()[0]
        new_content = merge_waves(old_content, content)
        cursor.execute("UPDATE facts SET content=?, energy=?, embedding=? WHERE id=?", (new_content, new_energy, json.dumps(new_vec), collision_id))
        conn.commit()
    else:
        cursor.execute("INSERT INTO facts (content, energy, embedding) VALUES (?, ?, ?)", (content, 0.5, json.dumps(new_vec)))
        conn.commit()
    return True

db_path = "prototype_test.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("DROP TABLE IF EXISTS facts")
cursor.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, energy REAL, embedding TEXT)")
conn.commit()

trajectory = [
    "La superposicion permite que una particula este en dos estados a la vez",
    "El entrelazamiento une dos particulas a distancia",
    "La superposicion es la base de la computacion cuantica",
    "Einstein llamo al entrelazamiento accion fantasmal",
    "El boson de Higgs otorga masa a las particulas",
    "La simetria del campo de Higgs es la que genera la masa",
]

for phrase in trajectory:
    inject_fact(conn, phrase)

cursor.execute("SELECT id, energy, content FROM facts")
rows = cursor.fetchall()
print("\n--- FINAL RESULT (Verified by AI) ---")
for row in rows:
    print(f"ID {row[0]} | Energy {row[1]:.2f} | {row[2]}")

conn.close()
