import json
import random
import os
from datetime import datetime, timedelta

# Configuración de archivos
FILE_100 = 'tests/membench_100.jsonl'
FILE_1000 = 'tests/membench_1000.jsonl'
NUM_CASES_100 = 100
NUM_CASES_1000 = 1000

# Categorías requeridas (5)
CATEGORIES = [
    'single_fact',
    'multi_fact',
    'contradiction',
    'archived_facts',
    'expect_empty'
]

# Casos de ejemplo base (adaptados de tests/membench_cases.jsonl)
# Nota: Se asume que existe un set de datos base más grande y se distribuye equitativamente.
# Usamos ejemplos simplificados para la generación sintética.
BASE_CASES = [
    # single_fact
    {"id": "single_fact_1", "category": "single_fact", "facts": ["Me llamo Ana García", "Trabajo en una panadería"], "question": "¿Cómo me llamo?", "expected": ["Ana García"]},
    # multi_fact
    {"id": "multi_fact_1", "category": "multi_fact", "facts": ["Mi vuelo a Oaxaca sale el 10 de marzo", "Mi vuelo a Oaxaca se reprogramó para el 24 de marzo"], "question": "¿Cuándo sale mi vuelo a Oaxaca?", "expected": ["24 de marzo"]},
    # contradiction
    {"id": "contradiction_1", "category": "contradiction", "facts": ["Mi vuelo a Oaxaca sale el 10 de marzo", "Mi vuelo a Oaxaca se reprogramó para el 24 de marzo"], "question": "¿Cuándo sale mi vuelo a Oaxaca?", "expected": ["24 de marzo"], "forbidden": ["10 de marzo"]},
    # archived_facts
    {"id": "archived_facts_1", "category": "archived_facts", "facts": [{"text": "El proyecto Ágora del semestre pasado era un foro para estudiantes", "age_days": 200}, "Este año quiero hacer una app de finanzas personales"], "question": "¿De qué trataba el proyecto Ágora?", "expected": ["foro"]},
    # expect_empty
    {"id": "expect_empty_1", "category": "expect_empty", "facts": ["Mi color favorito es el verde", "Trabajo en una panadería"], "question": "¿Cuál es el número de serie de mi refrigerador?", "expected": []}
]

def generate_synthetic_dataset(num_cases, filename):
    """Genera un dataset sintético distribuyendo equitativamente por categorías."""
    
    # Calcular cuántos casos por categoría
    cases_per_category = num_cases // len(CATEGORIES)
    remaining = num_cases % len(CATEGORIES)
    
    data = []
    
    # Generar casos
    for i, category in enumerate(CATEGORIES):
        count = cases_per_category + (1 if i < remaining else 0)
        
        # Generar variantes sintéticas basadas en el caso base
        for j in range(count):
            # Usamos un caso base y modificamos el ID para asegurar unicidad
            base_case = BASE_CASES[CATEGORIES.index(category) % len(BASE_CASES)]
            new_case = base_case.copy()
            new_case['id'] = f"{category}_{j+1}"
            
            # Asegurar que el número de hechos se ajuste a la categoría
            if category == 'single_fact':
                new_case['facts'] = [f"Hecho único #{j+1} para {category}"]
            elif category == 'multi_fact':
                new_case['facts'] = [f"Hecho 1 #{j+1}", f"Hecho 2 #{j+1}"]
            elif category == 'contradiction':
                new_case['facts'] = [f"Hecho contradictorio 1 #{j+1}", f"Hecho contradictorio 2 #{j+1}"]
                new_case['expected'] = [f"Resultado consistente #{j+1}"]
                new_case['forbidden'] = [f"Resultado inconsistente #{j+1}"]
            elif category == 'archived_facts':
                 new_case['facts'] = [{"text": f"Hecho archivado #{j+1}", "age_days": random.randint(100, 500)}, "Hecho actual de referencia"]
            elif category == 'expect_empty':
                new_case['facts'] = [f"Hecho base para {category} #{j+1}"]
                new_case['expected'] = []

            data.append(new_case)

    # Mezclar la lista final
    random.shuffle(data)

    # Escribir a JSONL
    with open(filename, 'w', encoding='utf-8') as f:
        for case in data:
            f.write(json.dumps(case, ensure_ascii=False) + '\n')
            
    print(f"✅ Dataset generado exitosamente en {filename} con {len(data)} casos.")
    return len(data)

if __name__ == '__main__':
    # Generar datasets
    print("--- Generando membench_100.jsonl ---")
    generate_synthetic_dataset(NUM_CASES_100, FILE_100)
    
    print("\n--- Generando membench_1000.jsonl ---")
    generate_synthetic_dataset(NUM_CASES_1000, FILE_1000)