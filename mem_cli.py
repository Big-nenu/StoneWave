import sys
import argparse
from pathlib import Path
from mem_engine import StoneWaveEngine


def _print_import_summary(s):
    print(f"Importación terminada. Nuevos: {s['new']}. Reforzados: {s['reinforced']} (ya existían). "
          f"Líneas inválidas: {len(s['invalid'])}. Errores de embedding: {s['errors']}.")
    if s["namespaces"]:
        print("Namespaces: " + ", ".join(s["namespaces"]))
    for line, why in s["invalid"][:10]:
        print(f"  Línea/registro {line}: {why}")
    if len(s["invalid"]) > 10:
        print(f"  ... y {len(s['invalid']) - 10} más")
    for msg in s["dream"]:
        print(msg)
    if s["aborted"]:
        print("Se detuvo: Ollama no respondió 3 veces seguidas. Revisa con:  python mem_cli.py doctor  y vuelve a correr el import "
              "(lo ya importado no se duplica, solo se refuerza).")


def main():
    parser = argparse.ArgumentParser(description="StoneWave CLI - Gestor de Memoria Semántica")
    parser.add_argument("command", choices=["inject", "query", "dream", "reflect", "clear", "doctor", "import", "export"],
                        help="Comando a ejecutar (doctor = reporte de salud; import/export = recuerdos en .jsonl)")
    parser.add_argument("text", nargs="?", help="Texto para inyectar o consultar; o el archivo .jsonl para import / export")
    parser.add_argument("-n", "--namespace", default=None,
                        help="Namespace/Proyecto (default: 'default'). En import, si lo das, TODO va a ese namespace; "
                             "si no, se respeta el de cada línea. En export, si no lo das, se exportan todos")
    parser.add_argument("-e", "--energy", type=float, default=0.5, help="Energía inicial para la inyección (default: 0.5)")

    parser.add_argument("-f", "--fijo", action="store_true", help="Recuerdo fijo: no decae ni se archiva")
    parser.add_argument("--from", dest="source_format", choices=["stonewave", "mcp-memory"], default="stonewave",
                        help="Formato del archivo a importar: 'stonewave' (.jsonl canónico, default) o "
                             "'mcp-memory' (memory.jsonl del MCP oficial de Anthropic)")
    parser.add_argument("--no-dream", action="store_true", help="En import: no correr el sueño al terminar")

    args = parser.parse_args()
    ns = args.namespace or "default"

    if args.command == "doctor":
        from doctor import run_doctor  # solo lectura: no abre el motor ni modifica nada
        sys.exit(run_doctor(ns))

    # Ruta profesional dinámica
    engine = StoneWaveEngine()

    if args.command == "inject":
        if not args.text:
            print("Error: El comando 'inject' requiere un texto.")
            return
        print(engine.inject(args.text, energy=args.energy, namespace=ns, pinned=args.fijo))

    elif args.command == "query":
        if not args.text:
            print("Error: El comando 'query' requiere un texto.")
            return
        res = engine.query(args.text, namespace=ns)
        if isinstance(res, str):
            print(res)
        elif res:
            for sim, fid, energy, content in res:
                print(f"Sim: {sim:.4f} | ID: {fid} | Energy: {energy:.2f} | {content}")
        else:
            print("No related memories found.")

    elif args.command == "dream":
        result = engine.dream(namespace=ns)
        if result:
            print(result)

    elif args.command == "reflect":
        result = engine.reflect(namespace=ns)
        if result:
            print(result)

    elif args.command == "clear":
        engine.clear_db(namespace=ns)
        print(f"Memoria limpiada para el namespace: {ns}")

    elif args.command == "import":
        if not args.text:
            print("Error: El comando 'import' requiere un archivo .jsonl.  Ej: python mem_cli.py import backup.jsonl")
            sys.exit(1)
        try:
            if args.source_format == "mcp-memory":
                from parsers.mcp_parser import load_records
                records, stats = load_records(args.text, namespace=ns)
                print(f"Convertido memory.jsonl del MCP: {len(records)} recuerdos "
                      f"({stats['duplicates']} repetidos y {stats['empty_entities']} entidades vacías omitidos)")
                summary = engine.import_records(records, namespace=args.namespace, run_dream=not args.no_dream,
                                                default_source="mcp-memory")
            else:
                summary = engine.import_memories(args.text, namespace=args.namespace, run_dream=not args.no_dream)
        except FileNotFoundError:
            print(f"No se encontró el archivo: {args.text}")
            sys.exit(1)
        _print_import_summary(summary)
        if summary["aborted"]:
            sys.exit(1)

    elif args.command == "export":
        if not args.text:
            print("Error: El comando 'export' requiere el archivo de salida.  Ej: python mem_cli.py export backup.jsonl")
            sys.exit(1)
        try:
            n = engine.export_memories(args.text, namespace=args.namespace)
        except OSError as ex:
            print(f"No se pudo escribir {args.text}: {ex}")
            sys.exit(1)
        print(f"Exportados {n} recuerdos a {args.text}" + (f" (namespace '{args.namespace}')" if args.namespace else " (todos los namespaces)"))

    else:
        print("Comando desconocido.")

if __name__ == "__main__":
    main()
