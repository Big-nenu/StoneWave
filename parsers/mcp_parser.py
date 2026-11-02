"""Adaptador: memory.jsonl del servidor MCP oficial de Anthropic (@modelcontextprotocol/server-memory)
-> formato canonico .jsonl de StoneWave.

El archivo de origen tiene una linea JSON por elemento del grafo:
  {"type": "entity",   "name": "Luis", "entityType": "person", "observations": ["Works at Neodata", ...]}
  {"type": "relation", "from": "Luis", "to": "Neodata", "relationType": "worksAt"}

Conversion (cada recuerdo de StoneWave es una frase suelta, asi que se le pone el nombre de la entidad):
  - cada observacion  -> "Luis: Works at Neodata"  (si la observacion ya empieza con el nombre, se deja igual)
  - cada relacion     -> "Luis works at Neodata"   (worksAt / works_at -> "works at")
  - entidades sin observaciones, lineas invalidas y frases repetidas se omiten (se cuentan en las estadisticas)

Uso:
  python parsers/mcp_parser.py memory.jsonl salida.jsonl [-n NAMESPACE] [--fijo NOMBRE ...] [--sin-relaciones]
  python mem_cli.py import memory.jsonl --from mcp-memory        # convierte e importa de una vez
"""
import argparse
import json
import re
import sys

SOURCE = "mcp-memory"


def _humanize(relation):
    """worksAt / works_at / works-at -> 'works at'."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(relation)).replace("_", " ").replace("-", " ")
    return " ".join(spaced.split()).lower()


def load_records(path, namespace="default", pinned_entities=(), include_relations=True):
    """Lee el memory.jsonl y devuelve (registros_canonicos, estadisticas).

    pinned_entities: nombres de entidades (sin importar mayusculas) cuyos recuerdos van como 'fijo'
    (por ejemplo tu propio nombre, para que nunca decaigan).
    """
    pinned = {str(n).strip().casefold() for n in pinned_entities}
    stats = {"entities": 0, "relations": 0, "empty_entities": 0, "invalid_lines": 0, "duplicates": 0}
    records, seen = [], set()

    def add(content, entity):
        content = " ".join(str(content).split())
        if not content:
            return
        if content.casefold() in seen:
            stats["duplicates"] += 1
            return
        seen.add(content.casefold())
        records.append({"content": content, "namespace": namespace, "fijo": entity.strip().casefold() in pinned,
                        "energy": 0.5, "source": SOURCE})

    with open(path, encoding="utf-8-sig") as f:  # utf-8-sig tolera el BOM de Windows
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                stats["invalid_lines"] += 1
                continue
            kind = item.get("type") if isinstance(item, dict) else None
            if kind == "entity" and isinstance(item.get("name"), str):
                name = item["name"].strip()
                obs = [o for o in item.get("observations") or [] if isinstance(o, str) and o.strip()]
                stats["entities"] += 1
                if not obs:
                    stats["empty_entities"] += 1
                for o in obs:
                    o = o.strip()
                    add(o if o.casefold().startswith(name.casefold()) else f"{name}: {o}", name)
            elif kind == "relation" and include_relations and all(isinstance(item.get(k), str) for k in ("from", "to", "relationType")):
                stats["relations"] += 1
                add(f"{item['from'].strip()} {_humanize(item['relationType'])} {item['to'].strip()}", item["from"])
            elif kind not in ("entity", "relation"):
                stats["invalid_lines"] += 1
    return records, stats


def convert(in_path, out_path, namespace="default", pinned_entities=(), include_relations=True):
    """Convierte in_path (memory.jsonl del MCP) a out_path (.jsonl canonico). Devuelve las estadisticas (+ 'records')."""
    records, stats = load_records(in_path, namespace, pinned_entities, include_relations)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["records"] = len(records)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convierte el memory.jsonl del MCP oficial de Anthropic al formato de StoneWave")
    ap.add_argument("entrada", help="memory.jsonl del servidor MCP memory")
    ap.add_argument("salida", help=".jsonl canonico de StoneWave que se va a crear")
    ap.add_argument("-n", "--namespace", default="default", help="namespace de destino (default: 'default')")
    ap.add_argument("--fijo", action="append", default=[], metavar="NOMBRE",
                    help="entidad cuyos recuerdos van como 'fijo' (se puede repetir; ej. --fijo Luis)")
    ap.add_argument("--sin-relaciones", action="store_true", help="ignora las relaciones, solo importa observaciones")
    args = ap.parse_args(argv)
    try:
        s = convert(args.entrada, args.salida, args.namespace, args.fijo, not args.sin_relaciones)
    except FileNotFoundError:
        print(f"No se encontró el archivo: {args.entrada}")
        return 1
    print(f"Listo: {s['records']} recuerdos escritos en {args.salida} "
          f"({s['entities']} entidades, {s['relations']} relaciones; omitidos: {s['empty_entities']} entidades sin observaciones, "
          f"{s['duplicates']} repetidos, {s['invalid_lines']} lineas invalidas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
