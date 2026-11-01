import subprocess

def run_cmd(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8').strip()

log_file = "debate_log.md"
with open(log_file, "w", encoding="utf-8") as f:
    f.write("# Debate: El futuro de la memoria humana y la IA\n---\n\n")

def add_to_log(persona, text):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"**{persona}:** {text}\n\n")

# Turnos del debate
turns = [
    ("El Optimista", "La IA permitirá que la memoria humana sea infinita. Podremos almacenar cada segundo de nuestra vida y recuperarlo al instante, eliminando el olvido."),
    ("El Escéptico", "Eso no es memoria, es un archivo. La memoria humana es valiosa precisamente porque olvida lo irrelevante y destila la esencia. Un disco duro no tiene alma."),
    ("El Optimista", "Pero imagina poder recuperar la voz de un abuelo o el sentimiento exacto de un primer viaje. La tecnología solo amplifica la capacidad de amar y recordar."),
    ("El Escéptico", "Al contrario, la nostalgia se alimenta de la imperfección. Si todo es perfecto y recuperable, la experiencia humana se vuelve plana y artificial."),
    ("El Optimista", "La síntesis de ambos mundos es el camino. Una memoria que nos ayude a no repetir errores, pero que nos permita seguir sintiendo el presente."),
    ("El Escéptico", "Dudo que el algoritmo entienda la diferencia entre un dato y un sentimiento. La memoria es una danza entre lo que ocurrió y lo que recordamos.")
]

for i, (persona, text) in enumerate(turns):
    # Inject into MemHolografic
    run_cmd(f'python mem_cli.py inject "{text}"')
    add_to_log(persona, text)
    
    if (i + 1) % 2 == 0:
        # Query synthesis
        res = run_cmd(f'python mem_cli.py query "memoria humana IA debate"')
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"***[SÍNTESIS DE MEMORIA - Turno {i+1}]***\n{res}\n\n")

print("Debate generated successfully.")
