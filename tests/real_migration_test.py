"""Prueba de la migracion de embeddings (necesita Ollama). Usa una BD temporal.
Ejecutar:  python tests/real_migration_test.py
"""
import os
import sys
import json
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mem_engine import MemHolograficEngine

db = os.path.join(tempfile.mkdtemp(), "m.db")
e = MemHolograficEngine(db_path=db)
e.inject("Me llamo Luis Eduardo", pinned=True)
e.inject("Trabajo en Neodata en Guadalajara")
e.inject("El helado de vainilla es rico")
e.close()

# Simula una base de datos vieja: otro esquema y vectores distintos
with sqlite3.connect(db) as c:
    c.execute("UPDATE meta SET value='esquema-viejo' WHERE key='embed_scheme'")
    c.execute("UPDATE facts SET embedding=?", (json.dumps([0.0] * 768),))
    c.execute("INSERT INTO archived_facts (namespace, content, energy, embedding, reason) VALUES ('default','Dato archivado',0.1,?, 'desuso')", (json.dumps([0.0] * 768),))
    c.commit()

e2 = MemHolograficEngine(db_path=db)  # aqui debe recalcular todo
with sqlite3.connect(db) as c:
    vecs = [json.loads(r[0]) for r in c.execute("SELECT embedding FROM facts UNION ALL SELECT embedding FROM archived_facts")]
    scheme = c.execute("SELECT value FROM meta WHERE key='embed_scheme'").fetchone()[0]
    contents = [r[0] for r in c.execute("SELECT content FROM facts")]
ok_vecs = all(any(abs(x) > 1e-6 for x in v) for v in vecs)
print("vectores recalculados:", ok_vecs, f"({len(vecs)} vectores)")
print("esquema actualizado:", scheme != "esquema-viejo", "->", scheme[:60])
print("respaldo creado:", os.path.exists(db + ".bak-embeddings"))
print("contenido intacto:", sorted(contents) == sorted(["Me llamo Luis Eduardo", "Trabajo en Neodata en Guadalajara", "El helado de vainilla es rico"]))
res = e2.query("¿Cómo me llamo?")
print("consulta despues de migrar:", res[0][3] if isinstance(res, list) else res)
e2.close()
