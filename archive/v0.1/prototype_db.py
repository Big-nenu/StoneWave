import sqlite3
import json
import requests
import math

# --- CONFIGURACIÓN ---
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBEDDING_MODEL = "nomic-embed-text:latest"

def get_embedding(text):
    try:
        # Intento 1: /api/embeddings
        r = requests.post(OLLAMA_URL, json={"model": EMBEDDING_MODEL, "prompt": text}, timeout=5)
        if r.status_code == 200:
            emb = r.json().get("embedding")
            if emb: return emb
        
        # Intento 2: /api/embed
        r = requests.post("http://localhost:11434/api/embed", json={"model": EMBEDDING_MODEL, "input": text}, timeout=5)
        if r.status_code == 200:
            res = r.json()
            emb_list = res.get("embeddings")
            if isinstance(emb_list, list) and len(emb_list) > 0:
                return emb_list[0]
            return res.get("embedding")
    except:
        pass
    return None

def calc_sim(v1, v2):
    if not v1 or not v2: return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    m1 = math.sqrt(sum(a * a for a in v1))
    m2 = math.sqrt(sum(a * a for a in v2))
    return dot / (m1 * m2) if m1 * m2 != 0 else 0.0

def merge_waves(t1, t2):
    t1_l, t2_l = t1.lower(), t2.lower()
    if "superposicion" in t1_l or "superposicion" in t2_l:
        return "CONSOLIDATED: La superposicion cuantica permite que la materia exista en multiplos estados simultaneos, siendo el pilar de la computacion cuantica."
    if "entrelazamiento" in t1_l or "entrelazamiento" in t2_l:
        return "CONSOLIDATED: El entrelazamiento es una conexion instantanea entre particulas a cualquier distancia, lo que Einstein llamo accion fantasmal."
    return f"MERGED: {t1} | {t2}"

def print_table(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, energy, content FROM facts")
    rows = cursor.fetchall()
    print("\n--- TABLA DE MEMORIA ---")
    print(f"{'ID':<5} | {'Energy':<10} | {'Content'}")
    print("-" * 100)
    for r in rows:
        e = float(r[1]) if r[1] is not None else 0.0
        print(f"{r[0]:<5} | {e:<10.2f} | {r[2]}")
    print("-" * 100)

def inject_fact(conn, content):
    print(f"\n>>> INYECTANDO: {content}")
    vec = get_embedding(content)
    if vec is None:
        print("❌ ERROR: No se obtuvo embedding de Ollama.")
        return
    
    cursor = conn.cursor()
    cursor.execute("SELECT id, content, embedding FROM facts")
    existing = cursor.fetchall()
    
    collision_id = None
    max_sim = 0.0
    for fid, fcontent, fvec_blob in existing:
        try:
            fvec = json.loads(fvec_blob)
            sim = calc_sim(vec, fvec)
            print(f"  - ID {fid}: Sim {sim:.4f}")
            if sim > 0.8:
                collision_id = fid
                max_sim = sim
                break
        except: continue
            
    if collision_id:
        print(f"🌊 COLISION con ID {collision_id}! (Sim: {max_sim:.4f})")
        cursor.execute("SELECT energy, content FROM facts WHERE id=?", (collision_id,))
        res = cursor.fetchone()
        if res:
            energy = float(res[0]) if res[0] is not None else 0.5
            old_content = res[1]
            new_energy = min(1.0, energy + 0.2)
            new_content = merge_waves(old_content, content)
            cursor.execute("UPDATE facts SET content=?, energy=?, embedding=? WHERE id=?", (new_content, new_energy, json.dumps(vec), collision_id))
            conn.commit()
            print("✨ FUSION EXITOSA.")
    else:
        print("📍 NUEVA ONDA.")
        cursor.execute("INSERT INTO facts (content, energy, embedding) VALUES (?, ?, ?)", (content, 0.5, json.dumps(vec)))
        conn.commit()

if __name__ == "__main__":
    db_path = "prototype_memory.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS facts")
    cursor.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, energy REAL, embedding TEXT)")
    conn.commit()
    print("\n=== CONTROL de MemHolografic (Súper Estable v4) ===")
    print("Instrucciones: Escribe una frase y presiona Enter.")
    print("Luegon ve al DB Browser, refresca la tabla 'facts' y vuelve aquí.")
    print("Escribe 'salir' para terminar.\n")
    while True:
        user_input = input("Siguiente onda >> ")
        if user_input.lower() in ['salir', 'exit', 'quit']:
            break
        inject_fact(conn, user_input)
        print_table(conn)
    conn.close()
