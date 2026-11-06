"""Compara modelos de decision (tev1 vs d1) en deteccion de contradicciones. Necesita Ollama.
Ejecutar:  python tests/compare_llm.py [modelo1 modelo2 ...]
No toca tu memoria ni el motor: solo llama a /v1/systemone.
"""
import sys
import time
import requests

URL = "http://localhost:11434/v1/systemone"
MODELS = sys.argv[1:] or ["tev1:0.8b", "d1:3b"]

# (a, b, esperado)  esperado: contradictorio | complementario | insuficiente
CASES = [
    ("Uso Windows en mi laptop", "Ahora uso Linux en mi laptop", "contradictorio"),
    ("La losa lleva concreto f'c=250 kg/cm²", "La losa lleva concreto f'c=150 kg/cm²", "contradictorio"),
    ("Trabajo actualmente en Neodata", "Ya no trabajo en Neodata", "contradictorio"),
    ("Mi perfume favorito es Versace Oud Noir", "Mi perfume favorito es Drakkar Noir", "contradictorio"),
    ("Mi hermana vive en Monterrey", "Mi hermana vive en Guadalajara", "contradictorio"),
    ("Trabajo en soporte técnico", "Desarrollo proyectos de IA en mi tiempo libre", "complementario"),
    ("Trabajo en Neodata", "Trabajo en una empresa de tecnología", "complementario"),
    ("Me llamo Luis", "Mi nombre completo es Luis Eduardo Hernández Vargas", "complementario"),
    ("Mi hermano se llama Pedro", "Mi hermano estudia medicina", "complementario"),
    ("Tengo un perro llamado Bruno", "Tengo un gato llamado Michi", "complementario"),
    ("Estudié en Lamar de 2009 a 2013", "Me gradué de Lamar en 2013", "complementario"),
    ("Mi laptop de casa tiene 32 GB de RAM", "La PC de mi oficina tiene 16 GB de RAM", "complementario"),
    ("Mi computadora tiene 32 GB de RAM", "Mi computadora tiene 16 GB de RAM", "insuficiente"),
    ("Mi examen es el viernes", "Mi examen es el lunes", "insuficiente"),
    ("Mi teléfono tiene 128 GB", "Mi computadora tiene 512 GB", "complementario"),
]

CRIT2 = {
    "contradictorio": "La Frase 2 contradice o invalida la Frase 1",
    "complementario": "Ambas frases pueden coexistir o se complementan",
}
CRIT3 = {
    "contradictorio": "Hablan claramente de lo mismo (mismo objeto, persona y momento) y la Frase 2 anula a la Frase 1",
    "complementario": "Ambas frases pueden ser ciertas a la vez",
    "insuficiente": "No se sabe si hablan del mismo objeto, persona o momento; no se puede asegurar que se contradigan",
}
CRIT4 = {
    "contradictorio": "Hablan del mismo tema o dato y dan valores incompatibles, o la Frase 2 dice que la Frase 1 ya no es cierta",
    "complementario": "Aportan datos distintos que pueden ser ciertos a la vez",
    "insuficiente": "Podrian referirse a objetos, personas o momentos distintos y no hay forma de saberlo",
}
INSTR4 = "¿Que relacion hay entre la Frase 1 y la Frase 2? Elige insuficiente solo si de verdad podrian hablar de cosas distintas."
INSTR2 = "Decide si la Frase 2 anula o contradice a la Frase 1, o si ambas pueden ser ciertas a la vez."
INSTR3 = ("Usa solo la informacion explicita. No supongas que ambas frases hablan del mismo objeto, persona o momento. "
          "¿Que relacion esta demostrada entre la Frase 1 y la Frase 2?")


def ask(model, a, b, crit, instr):
    payload = {"model": model, "state": f"Frase 1: {a}\nFrase 2: {b}",
               "questions": {"r": {"type": "choice", "instructions": instr, "criteria": crit}}}
    t = time.perf_counter()
    try:
        r = requests.post(URL, json=payload, timeout=180)
        ans = r.json()["answers"]["r"]
        return ans["choice"], float(ans.get("confidence", 0)), (time.perf_counter() - t) * 1000
    except Exception as ex:
        return f"ERR:{str(ex)[:30]}", 0.0, (time.perf_counter() - t) * 1000


def both_orders(model, a, b, crit, instr):
    v1, c1, t1 = ask(model, a, b, crit, instr)
    if v1 != "contradictorio":
        return v1, c1, t1
    v2, c2, t2 = ask(model, b, a, crit, instr)
    return (v1 if v2 == "contradictorio" else "complementario" if "insuficiente" not in crit else v2), min(c1, c2), t1 + t2


def run():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    for model in MODELS:
        ask(model, "hola", "adios", CRIT2, INSTR2)  # calienta (carga el modelo)
        for label, crit, instr in (("2 opciones (como hoy)", CRIT2, INSTR2), ("3 opciones + 'no supongas'", CRIT3, INSTR3), ("3 opciones SUAVE", CRIT4, INSTR4)):
            print(f"\n=== {model} | {label} ===")
            ok = fp = fn = 0
            times = []
            for a, b, exp in CASES:
                v, conf, ms = both_orders(model, a, b, crit, instr)
                times.append(ms)
                good = (v == "contradictorio") if exp == "contradictorio" else (v != "contradictorio")
                strict = v == exp
                ok += good
                if exp != "contradictorio" and v == "contradictorio":
                    fp += 1
                if exp == "contradictorio" and v != "contradictorio":
                    fn += 1
                mark = "OK" if strict else ("~ok" if good else "XX")
                print(f"  {mark:<4} esp={exp:<15} dio={v:<15} conf={conf:.2f} {ms:5.0f} ms | {a[:34]} / {b[:30]}")
            print(f"  SEGURO (no archiva de mas): {len(CASES) - fp}/{len(CASES)} | Falsos positivos: {fp} | "
                  f"Contradicciones perdidas: {fn} | Aciertos: {ok}/{len(CASES)} | ms medio: {sum(times)/len(times):.0f}")


if __name__ == "__main__":
    run()
