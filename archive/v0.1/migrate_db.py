
import sqlite3
db_path = "prototype_memory.db"
with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    # Check if column exists
    cursor.execute("PRAGMA table_info(facts)")
    cols = [col[1] for col in cursor.fetchall()]
    if "namespace" not in cols:
        print("Adding namespace to facts...")
        cursor.execute("ALTER TABLE facts ADD COLUMN namespace TEXT DEFAULT 'default'")
    
    cursor.execute("PRAGMA table_info(archived_facts)")
    cols_arch = [col[1] for col in cursor.fetchall()]
    if "namespace" not in cols_arch:
        print("Adding namespace to archived_facts...")
        cursor.execute("ALTER TABLE archived_facts ADD COLUMN namespace TEXT DEFAULT 'default'")
    conn.commit()
print("Migration completed.")
