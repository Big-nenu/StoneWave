import sys
import argparse
from pathlib import Path
from mem_engine import StoneWaveEngine

def main():
    parser = argparse.ArgumentParser(description="StoneWave CLI - Gestor de Memoria Semántica")
    parser.add_argument("command", choices=["inject", "query", "dream", "reflect", "clear"], help="Comando a ejecutar")
    parser.add_argument("text", nargs="?", help="Texto para inyectar o consultar")
    parser.add_argument("-n", "--namespace", default="default", help="Namespace/Proyecto para la operación (default: 'default')")
    parser.add_argument("-e", "--energy", type=float, default=0.5, help="Energía inicial para la inyección (default: 0.5)")

    parser.add_argument("-f", "--fijo", action="store_true", help="Recuerdo fijo: no decae ni se archiva")

    args = parser.parse_args()
    
    # Ruta profesional dinámica
    engine = StoneWaveEngine()

    if args.command == "inject":
        if not args.text:
            print("Error: El comando 'inject' requiere un texto.")
            return
        print(engine.inject(args.text, energy=args.energy, namespace=args.namespace, pinned=args.fijo))
    
    elif args.command == "query":
        if not args.text:
            print("Error: El comando 'query' requiere un texto.")
            return
        res = engine.query(args.text, namespace=args.namespace)
        if isinstance(res, str):
            print(res)
        elif res:
            for sim, fid, energy, content in res:
                print(f"Sim: {sim:.4f} | ID: {fid} | Energy: {energy:.2f} | {content}")
        else:
            print("No related memories found.")
            
    elif args.command == "dream":
        result = engine.dream(namespace=args.namespace)
        if result:
            print(result)
            
    elif args.command == "reflect":
        result = engine.reflect(namespace=args.namespace)
        if result:
            print(result)
    
    elif args.command == "clear":
        engine.clear_db(namespace=args.namespace)
        print(f"Memoria limpiada para el namespace: {args.namespace}")
    
    else:
        print("Comando desconocido.")

if __name__ == "__main__":
    main()
