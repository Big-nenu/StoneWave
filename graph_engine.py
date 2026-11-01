import os
import json
import requests
import sqlite3
from pathlib import Path

# Config for Graph Engine
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
SYNTHESIS_MODEL = os.getenv("MEMHOLOGRAFIC_SYNTHESIS_MODEL", "qwen2.5:0.5b")
GRAPH_MODEL = os.getenv("MEMHOLOGRAFIC_GRAPH_MODEL", SYNTHESIS_MODEL)
GRAPH_OUTPUT_PATH = os.getenv("MEMHOLOGRAFIC_GRAPH_OUTPUT", "memoria_grafo.html")

class GraphEngine:
    def __init__(self, db_path):
        self.db_path = db_path

    def _normalize_entity_name(self, nombre, user_name="User"):
        """Normaliza nombres de entidades. El nombre del usuario es la prioridad."""
        nombre_lower = nombre.strip().lower()
        # Dinámicamente unificamos cualquier mención al usuario actual
        if nombre_lower == user_name.lower() or nombre_lower == "usuario":
            return user_name
        return nombre.strip()

    def extract_graph_entities(self, text, user_name="User"):
        """Usa qwen2.5:0.5b para extraer entidades y relaciones de un texto."""
        prompt = f"""Eres un extractor de entidades y relaciones para un grafo del conocimiento.
Analiza el siguiente texto y devuelve SOLO un JSON válido con esta estructura:
{{"entidades": [{{"nombre": "...", "tipo": "..."}}], "relaciones": [{{"origen": "...", "destino": "...", "relacion": "..."}}]}}

Reglas importantes:
- Identifica al usuario principal ({user_name}) como entidad de tipo "persona".
- Extrae todas las entidades nombradas: proyectos, tecnologías, lugares, conceptos, juegos.
- Tipos de entidad válidos: persona, proyecto, tecnologia, lugar, concepto, habilidad, juego.
- Relaciones válidas: trabaja_en, estudio_en, usa, vive_en, gusta_de, interesa_en, escribio, creo, es_de, relacionado_con.
- Si hay una persona, crea relaciones DESDE esa persona HACIA las otras entidades.
- No inventes entidades que no estén en el texto.
- Si no hay entidades claras, devuelve arrays vacíos.

Texto: {text}

JSON:"""
        try:
            r = requests.post(OLLAMA_GEN_URL, json={"model": GRAPH_MODEL, "prompt": prompt, "stream": False}, timeout=30)
            if r.status_code == 200:
                response = r.json().get("response", "").strip()
                start = response.find("{")
                end = response.rfind("}")
                if start != -1 and end != -1 and end > start:
                    json_str = response[start:end+1]
                    import json as _json
                    data = _json.loads(json_str)
                    entidades = data.get("entidades", [])
                    relaciones = data.get("relaciones", [])
                    resultado = []
                    for rel in relaciones:
                        origen = self._normalize_entity_name(rel.get("origen", ""), user_name)
                        destino = self._normalize_entity_name(rel.get("destino", ""), user_name)
                        if origen and destino:
                            resultado.append((
                                origen,
                                destino,
                                rel.get("relacion", "relacionado_con"),
                                text[:80]
                            ))
                    entidades_normalizadas = []
                    for ent in entidades:
                        nombre = self._normalize_entity_name(ent.get("nombre", ""), user_name)
                        tipo = ent.get("tipo", "concepto")
                        if nombre:
                            entidades_normalizadas.append({"nombre": nombre, "tipo": tipo})
                    return resultado, entidades_normalizadas
        except Exception:
            pass
        return [], []

    def _get_central_person(self, namespace, user_name="User"):
        """Intenta identificar a la persona principal del namespace."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT nombre FROM entities WHERE tipo='persona' ORDER BY id LIMIT 1")
            row = cursor.fetchone()
            if row:
                return row[0]
            cursor.execute("SELECT content FROM facts WHERE namespace=? ORDER BY id", (namespace,))
            for (content,) in cursor.fetchall():
                if user_name in content:
                    return user_name
        return user_name

    def build_graph_from_facts(self, namespace, user_name="User"):
        """Extrae entidades y relaciones de todos los hechos activos del namespace."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM relationships")
            cursor.execute("DELETE FROM entities")
            conn.commit()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, content FROM facts WHERE namespace=? ORDER BY id", (namespace,))
            facts = cursor.fetchall()

        if not facts:
            return {"entities": [], "relationships": [], "mensaje": "No hay hechos para graficar."}

        all_entities = {}
        all_relationships = []
        central_person = self._get_central_person(namespace, user_name)

        for fact_id, content in facts:
            relaciones, entidades = self.extract_graph_entities(content, user_name)
            for ent in entidades:
                nombre = ent.get("nombre", "")
                tipo = ent.get("tipo", "concepto")
                if nombre and nombre not in all_entities:
                    all_entities[nombre] = {"nombre": nombre, "tipo": tipo, "embedding": None}
            for origen, destino, tipo_rel, contexto in relaciones:
                if origen and destino:
                    all_relationships.append({
                        "origen": origen,
                        "destino": destino,
                        "relacion": tipo_rel,
                        "hecho_id": fact_id,
                        "contexto": contexto
                    })

        if central_person:
            if central_person not in all_entities:
                all_entities[central_person] = {"nombre": central_person, "tipo": "persona", "embedding": None}
            for nombre in all_entities:
                if nombre != central_person:
                    ya_existe = any(r["origen"] == central_person and r["destino"] == nombre for r in all_relationships)
                    if not ya_existe:
                        all_relationships.append({
                            "origen": central_person,
                            "destino": nombre,
                            "relacion": "relacionado_con",
                            "hecho_id": None,
                            "contexto": "conexión implícita desde persona central"
                        })

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for nombre, data in all_entities.items():
                cursor.execute("INSERT OR IGNORE INTO entities (nombre, tipo, embedding) VALUES (?, ?, ?)",
                               (nombre, data["tipo"], json.dumps(data["embedding"])))
            for rel in all_relationships:
                cursor.execute("INSERT INTO relationships (entidad_origen, entidad_destino, tipo_relacion, hecho_origen_id, peso) VALUES (?, ?, ?, ?, ?)",
                               (rel["origen"], rel["destino"], rel["relacion"], rel["hecho_id"], 1.0))
            conn.commit()

        return {
            "entities": list(all_entities.values()),
            "relationships": all_relationships,
            "mensaje": f"Grafo construido: {len(all_entities)} entidades, {len(all_relationships)} relaciones."
        }

    def generate_graph_html(self, namespace, user_name="User"):
        """Genera un archivo HTML con vis-network para visualizar el grafo."""
        grafo = self.build_graph_from_facts(namespace, user_name)
        entities = grafo.get("entities", [])
        relationships = grafo.get("relationships", [])
        nodos_js = []
        for ent in entities:
            color = self._entity_color(ent.get("tipo", "concepto"))
            nodos_js.append(f"{{id: {len(nodos_js)}, label: {json.dumps(ent['nombre'])}, title: {json.dumps(ent.get('tipo','concepto'))}, color: '{color}', borderWidth: 3}}")
        aristas_js = []
        for rel in relationships:
            origen_idx = next((i for i, e in enumerate(entities) if e["nombre"] == rel["origen"]), None)
            destino_idx = next((i for i, e in enumerate(entities) if e["nombre"] == rel["destino"]), None)
            if origen_idx is not None and destino_idx is not None:
                edge_color = self._relation_color(rel.get("relacion", "relacionado_con"))
                aristas_js.append(f"{{from: {origen_idx}, to: {destino_idx}, label: {json.dumps(rel['relacion'])}, arrows: 'to', color: '{edge_color}', font: {{ color: '{edge_color}' }}, smooth: {{ type: 'continuous', roundness: 0.2 }}, width: 2}}")
        
        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>StoneWave - Grafo de Memoria</title>
<script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style type="text/css">
  body {{ margin: 0; overflow: hidden; font-family: sans-serif; background: #0a0a0a; color: #eee; }}
  #mynetwork {{ width: 100vw; height: 100vh; position: absolute; top: 0; left: 0; background: #0a0a0a; }}
  #info {{ position: absolute; top: 10px; left: 10px; z-index: 100; background: rgba(20, 20, 20, 0.9); padding: 15px; border-radius: 8px; max-width: 450px; border: 1px solid #333; }}
  h1 {{ margin: 0 0 10px 0; font-size: 18px; color: #fff; }}
  p {{ margin: 5px 0; font-size: 13px; color: #aaa; }}
  strong {{ color: #fff; }}
</style>
</head>
<body>
<div id="info">
  <h1>🧠 StoneWave - Grafo de Memoria</h1>
  <p><strong>Entidades:</strong> {len(entities)}</p>
  <p><strong>Relaciones:</strong> {len(relationships)}</p>
  <p><strong>Namespace:</strong> {namespace}</p>
  <p><small>Haz clic en los nodos para explorar. Arrastra para mover.</small></p>
</div>
<div id="mynetwork"></div>
<script type="text/javascript">
  var nodos = new vis.DataSet([{", ".join(nodos_js)}]);
  var aristas = new vis.DataSet([{", ".join(aristas_js)}]);
  var container = document.getElementById("mynetwork");
  var data = {{ nodes: nodos, edges: aristas }};
  var opciones = {{
    nodes: {{ shape: 'dot', size: 16, font: {{ size: 14 }}, borderWidth: 2 }},
    edges: {{ font: {{ size: 12, align: 'middle' }}, arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }}, smooth: {{ type: 'continuous' }} }},
    physics: {{ enabled: true, solver: 'forceAtlas2Based', stabilization: {{ iterations: 100 }} }},
    interaction: {{ hover: true, tooltipDelay: 200 }}
  }};
  var red = new vis.Network(container, data, opciones);
  red.on("click", function (props) {{
    if (props.nodes.length > 0) {{
      var nodoId = props.nodes[0];
      var nodo = nodos.get(nodoId);
      console.log("Nodo seleccionado: " + nodo.label);
    }}
  }});
</script>
</body>
</html>"""
        output_path = GRAPH_OUTPUT_PATH
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return {"output_path": output_path, "entities": len(entities), "relationships": len(relationships), "mensaje": "Grafo generado."}

    def _entity_color(self, tipo):
        colores = {
            "persona": "#ff4d4d", "proyecto": "#00d2ff", "tecnologia": "#00ff88",
            "lugar": "#ff9900", "concepto": "#cc66ff", "habilidad": "#ff66cc",
            "juego": "#aa66ff", "otro": "#888888"
        }
        return colores.get(tipo, "#888888")

    def _relation_color(self, tipo_rel):
        colores = {
            "trabaja_en": "#ff4d4d", "estudio_en": "#00d2ff", "usa": "#00ff88",
            "vive_en": "#ff9900", "gusta_de": "#ff66cc", "interesa_en": "#cc66ff",
            "escribio": "#aa66ff", "creo": "#ff9900", "es_de": "#00d2ff",
            "relacionado_con": "#888888"
        }
        return colores.get(tipo_rel, "#888888")
