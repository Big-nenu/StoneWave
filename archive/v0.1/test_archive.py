import sqlite3, json
from mem_engine import MemHolograficEngine

engine = MemHolograficEngine("test_archive.db")

# 1. Inject memories
print("Injecting memories...")
engine.inject("El cielo es azul")
engine.inject("El helado de vainilla es rico")

# Manually set the energy of the second one to be very low
with sqlite3.connect("test_archive.db") as conn:
    cursor = conn.cursor()
    cursor.execute("UPDATE facts SET energy = 0.2 WHERE content = 'El helado de vainilla es rico'")
    conn.commit()

print("\n--- Active Memory ---")
engine.print_table("facts")

# 2. Run Dream to archive low energy
print("\nRunning Dream...")
print(engine.dream())

print("\n--- Active Memory after Dream ---")
engine.print_table("facts")
print("\n--- Archived Memory ---")
engine.print_table("archived_facts")

# 3. Query for the forgotten memory
print("\nQuerying for 'helado'...")
res = engine.query("helado de vainilla")
print(f"Result: {res}")

print("\n--- Active Memory after Resurrection ---")
engine.print_table("facts")
