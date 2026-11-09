"""Pruebas de las notas de Obsidian SIN Ollama (embeddings y modelos falsos).
Ejecutar:  python tests/test_notes.py
"""
import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import make_engine, fake_embedding
import mem_engine
import notes

F1 = "Luis aprende mejor haciendo ejercicios prácticos de programación"
F2 = "Luis aprende mejor haciendo ejercicios prácticos de matemáticas"
F3 = "Luis aprende mejor haciendo ejercicios prácticos de física"
CONC1 = "Luis aprende mejor haciendo ejercicios prácticos"
CONC2 = "Luis estudia ciencias con ejemplos prácticos"
EMPTY = {"namespace": "default", "conclusions": [], "facts": {}, "relations": {}}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def path_of(base, folder, kind, text):
    return os.path.join(base, folder, notes.note_name(kind, text) + ".md")


def dos_conclusiones():
    """Dos conclusiones que comparten recuerdos: CONC1 (de F1 y F2, que luego respalda F3) y CONC2 (de F1, F2 y F3)."""
    e = make_engine()
    calls = []
    texts = [CONC1, CONC2]
    e._ask_llm_conclusion = lambda f: (calls.append(1), texts[len(calls) - 1] if len(calls) <= 2 else "")[1]
    e._ask_llm_support = lambda f, c: "RESPALDA"
    e.inject(F1)
    e.inject(F2)
    e.dream()
    e.inject(F3)
    e.dream()
    return e


def con_texto(text):
    e = make_engine()
    e._ask_llm_conclusion = lambda f: text
    e._ask_llm_support = lambda f, c: "RESPALDA"
    e.inject(F1)
    e.inject(F2)
    e.dream()
    return e


def all_links_resolve(base):
    names = {f[:-3] for d in ("conclusiones", "recuerdos") for f in os.listdir(os.path.join(base, d))}
    for d in ("conclusiones", "recuerdos"):
        for f in os.listdir(os.path.join(base, d)):
            targets = re.findall(r"\[\[([^\]|]+)\|", read(os.path.join(base, d, f)))
            assert targets and all(t in names for t in targets), (f, targets)


def test_notas_carpetas_separadas_y_wikilinks_que_apuntan_a_notas_reales():
    e = dos_conclusiones()
    assert len(e.list_conclusions()) == 2
    base = tempfile.mkdtemp()
    res = e.export_notes(base_dir=base)
    assert res["status"] == "ok" and res["conclusions"] == 2 and res["memories"] == 3 and res["written"] == 5, res
    assert sorted(os.listdir(base)) == ["conclusiones", "recuerdos"]  # nada fuera de las dos carpetas
    assert len(os.listdir(os.path.join(base, "conclusiones"))) == 2 and len(os.listdir(os.path.join(base, "recuerdos"))) == 3
    all_links_resolve(base)

    c1 = read(path_of(base, "conclusiones", "conclusion", CONC1))
    assert 'tipo: "conclusion"' in c1 and 'estado: "hipotesis"' in c1 and 'generado_por: "stonewave"' in c1, c1
    assert 'tags: ["stonewave", "conclusion", "hipotesis"]' in c1, c1
    viene = c1.split("## Viene de")[1].split("## Relacionada con")[0]
    assert viene.count("[[") == 3 and "Luis aprende mejor haciendo ejercicios prácticos de física" in viene, viene  # trazabilidad: a sus recuerdos
    rel = c1.split("## Relacionada con")[1]
    assert notes.note_name("conclusion", CONC2) in rel and "comparten 3 recuerdo(s)" in rel, rel  # y a la otra conclusion

    m1 = read(path_of(base, "recuerdos", "recuerdo", F1))
    assert 'tipo: "recuerdo"' in m1 and 'temas: ["otro"]' in m1 and "energia" not in m1, m1  # sin datos que cambian a cada rato
    sirvio = m1.split("## Sirvió para")[1]
    assert sirvio.count("[[") == 2 and notes.note_name("conclusion", CONC1) in sirvio, sirvio  # y de vuelta a las conclusiones


def test_notas_la_fuente_de_verdad_es_la_memoria_y_nada_vuelve_a_ella():
    e = dos_conclusiones()
    base = tempfile.mkdtemp()
    e.export_notes(base_dir=base)
    snapshot = (e.list_facts(), e.list_conclusions())
    editadas = [path_of(base, "conclusiones", "conclusion", CONC1), path_of(base, "recuerdos", "recuerdo", F1)]
    for p in editadas:
        with open(p, "a", encoding="utf-8") as f:
            f.write("\nMI EDICION EN OBSIDIAN\n")
    res = e.export_notes(base_dir=base)
    assert res["written"] == 2 and res["unchanged"] == 3, res  # lo editado se sobrescribe
    assert all("MI EDICION" not in read(p) for p in editadas)
    assert (e.list_facts(), e.list_conclusions()) == snapshot  # la memoria no se entero de la edicion


def test_notas_exportar_dos_veces_no_cambia_nada():
    e = dos_conclusiones()
    base = tempfile.mkdtemp()
    e.export_notes(base_dir=base)
    res = e.export_notes(base_dir=base)
    assert res["written"] == 0 and res["unchanged"] == 5 and res["deleted"] == 0, res


def test_notas_se_actualizan_con_el_estado_y_se_limpian_las_que_ya_no_existen():
    e = dos_conclusiones()
    base = tempfile.mkdtemp()
    e.export_notes(base_dir=base)
    with sqlite3.connect(e.db_path) as c:
        c.execute("UPDATE conclusions SET status='confirmada', energy=0.9 WHERE content=?", (CONC1,))
        c.commit()
    res = e.export_notes(base_dir=base)
    assert res["written"] == 5, res  # la conclusion, la otra que la nombra y los 3 recuerdos que la nombran
    c1 = read(path_of(base, "conclusiones", "conclusion", CONC1))
    assert 'estado: "confirmada"' in c1 and "stonewave\", \"conclusion\", \"confirmada\"" in c1, c1
    assert "(confirmada)" in read(path_of(base, "recuerdos", "recuerdo", F1))
    assert "(confirmada)" in read(path_of(base, "conclusiones", "conclusion", CONC2))

    cid2 = [c["id"] for c in e.list_conclusions() if c["content"] == CONC2][0]
    assert e.forget_conclusion(cid2)["status"] == "deleted"
    res = e.export_notes(base_dir=base)
    assert res["deleted"] == 1 and len(os.listdir(os.path.join(base, "conclusiones"))) == 1, res
    assert len(os.listdir(os.path.join(base, "recuerdos"))) == 3
    assert "[[" + notes.note_name("conclusion", CONC2) not in read(path_of(base, "conclusiones", "conclusion", CONC1))
    all_links_resolve(base)

    cid1 = e.list_conclusions()[0]["id"]
    e.forget_conclusion(cid1)
    res = e.export_notes(base_dir=base)  # sin conclusiones tampoco quedan notas de recuerdos
    assert res["deleted"] == 4 and os.listdir(os.path.join(base, "conclusiones")) == [] and os.listdir(os.path.join(base, "recuerdos")) == [], res
    assert len(e.list_facts()) == 3  # y los recuerdos siguen en la memoria


def test_notas_no_toca_lo_que_no_es_suyo():
    e = dos_conclusiones()
    base = tempfile.mkdtemp()
    conc = os.path.join(base, "conclusiones")
    os.makedirs(conc)
    mia_mismo_nombre = path_of(base, "conclusiones", "conclusion", CONC1)  # una nota tuya que se llama igual que una generada
    suya = os.path.join(conc, "Mi nota personal.md")
    de_otra_pc = os.path.join(conc, "Otra conclusion (abc123).md")
    vieja_mia = os.path.join(conc, "Vieja (def456).md")
    for p, text in [(mia_mismo_nombre, "# mi nota\nescrita por mi\n"), (suya, "# personal\n"),
                    (de_otra_pc, '---\ngenerado_por: "stonewave"\nequipo: "otra-pc"\n---\n# x\n'),
                    (vieja_mia, f'---\ngenerado_por: "stonewave"\nequipo: {json.dumps(notes.this_host())}\n---\n# y\n')]:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    res = e.export_notes(base_dir=base)
    assert res["skipped"] == [os.path.basename(mia_mismo_nombre)] and res["written"] == 4, res
    assert read(mia_mismo_nombre) == "# mi nota\nescrita por mi\n" and os.path.exists(suya) and os.path.exists(de_otra_pc)
    assert not os.path.exists(vieja_mia) and res["deleted"] == 1  # solo se borra lo generado por este equipo que ya no corresponde


def test_notas_varias_computadoras_comparten_carpeta_sin_borrarse_entre_si():
    e = dos_conclusiones()
    base = tempfile.mkdtemp()
    data = e.notes_data()
    assert notes.write_notes(data, base, host="PC-A")["written"] == 5
    assert notes.write_notes(EMPTY, base, host="PC-B")["deleted"] == 0  # B no tiene conclusiones, pero no borra las de A
    assert len(os.listdir(os.path.join(base, "conclusiones"))) == 2
    assert notes.write_notes(data, base, host="PC-B")["written"] == 5  # el mismo texto es el mismo archivo: B lo toma
    assert notes.write_notes(EMPTY, base, host="PC-A")["deleted"] == 0  # y ahora A ya no es la dueña
    assert notes.write_notes(EMPTY, base, host="PC-B")["deleted"] == 5


def test_notas_nombres_seguros_estables_y_wikilinks_validos():
    raro = 'Le gusta: ¿café? <sí> | "no" #tag [x] ^y \\ / fin'
    name = notes.note_name("conclusion", raro)
    assert not re.search(r'[\\/:*?"<>|#^\[\]]', name) and re.search(r" \([0-9a-f]{6}\)$", name), name
    assert name == notes.note_name("conclusion", raro)
    assert name[-8:] == notes.note_name("conclusion", "  " + raro.upper() + " ")[-8:]  # la huella no depende de mayusculas ni espacios
    assert name != notes.note_name("recuerdo", raro)
    assert len(notes.note_name("conclusion", "palabra " * 80)) <= 70 + 9
    assert re.fullmatch(r"nota \([0-9a-f]{6}\)", notes.note_name("recuerdo", "..."))
    assert notes.link("a (b12345)", "x | [y]") == "[[a (b12345)|x / (y)]]"
    e = con_texto(raro)
    base = tempfile.mkdtemp()
    assert e.export_notes(base_dir=base)["conclusions"] == 1
    all_links_resolve(base)
    assert read(path_of(base, "conclusiones", "conclusion", raro)).splitlines()[0] == "---"


def test_notas_relacion_parecida_aunque_no_compartan_recuerdos():
    e = make_engine()
    for text in ("Compré un perfume nuevo de cuero", "Mi perfume favorito es amaderado", "Practico guitarra todas las tardes", "Veo videos de guitarra eléctrica"):
        e.inject(text)
    ids = {c: i for i, c, _en, _p in e.list_facts()}
    c1, c2 = "Prefiere perfumes amaderados con cuero", "Prefiere perfumes amaderados con vainilla"
    with sqlite3.connect(e.db_path) as c:
        for n, (text, srcs) in enumerate([(c1, ["Compré un perfume nuevo de cuero", "Mi perfume favorito es amaderado"]),
                                          (c2, ["Practico guitarra todas las tardes", "Veo videos de guitarra eléctrica"])], 1):
            c.execute("INSERT INTO conclusions (namespace, content, energy, embedding, status, created_at, last_checked) VALUES "
                      "('default', ?, 0.3, ?, 'hipotesis', 0, 0)", (text, json.dumps(fake_embedding(text))))
            c.executemany("INSERT INTO conclusion_sources VALUES (?, ?)", [(n, ids[s]) for s in srcs])
        c.commit()
    base = tempfile.mkdtemp()
    e.export_notes(base_dir=base)
    note = read(path_of(base, "conclusiones", "conclusion", c1))
    assert notes.note_name("conclusion", c2) in note and "parecida (similitud 0.80)" in note, note
    assert "comparten" not in note
    assert "parecida (similitud 0.80)" in read(path_of(base, "conclusiones", "conclusion", c2))  # y en las dos direcciones


def test_notas_apagadas_por_defecto_y_el_sueno_las_escribe_solo_si_se_activan():
    old = os.environ.get("STONEWAVE_NOTES")
    try:
        os.environ.pop("STONEWAVE_NOTES", None)
        e = make_engine()
        e._ask_llm_conclusion = lambda f: CONC1
        e._ask_llm_support = lambda f, c: "RESPALDA"
        e.inject(F1)
        e.inject(F2)
        assert notes.notes_dir() is None and e.export_notes()["status"] == "off"
        assert "Notas de Obsidian" not in e.dream()
        with sqlite3.connect(e.db_path) as c:
            c.execute("UPDATE facts SET reasoned=0")
            c.commit()
        base = tempfile.mkdtemp()
        os.environ["STONEWAVE_NOTES"] = base
        out = e.dream()
        assert "Notas de Obsidian: 3 escritas" in out, out
        assert os.path.exists(path_of(base, "conclusiones", "conclusion", CONC1))
        os.environ["STONEWAVE_NOTES"] = os.path.join(base, "no-existe")
        assert "no existe" in e.dream()  # una carpeta mal escrita no tira el sueño ni crea carpetas
        assert not os.path.exists(os.path.join(base, "no-existe"))
        os.environ["STONEWAVE_NOTES"] = "off"
        assert notes.notes_dir() is None
    finally:
        if old is None:
            os.environ.pop("STONEWAVE_NOTES", None)
        else:
            os.environ["STONEWAVE_NOTES"] = old


def test_notas_comando_notes_de_la_cli():
    import mem_cli
    e = dos_conclusiones()
    old_engine, old_argv, old_env = mem_cli.StoneWaveEngine, sys.argv, os.environ.pop("STONEWAVE_NOTES", None)
    mem_cli.StoneWaveEngine = lambda: e

    def run(*args):
        sys.argv = ["mem_cli.py", *args]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                mem_cli.main()
                code = 0
            except SystemExit as ex:
                code = ex.code or 0
        return code, buf.getvalue()
    try:
        code, out = run("notes")
        assert code == 1 and "STONEWAVE_NOTES" in out, out
        base = tempfile.mkdtemp()
        code, out = run("notes", base)
        assert code == 0 and "2 conclusiones y 3 recuerdos" in out and "5 escritas" in out, out
        code, out = run("notes", os.path.join(base, "nada"))
        assert code == 1 and "no existe" in out, out
    finally:
        mem_cli.StoneWaveEngine, sys.argv = old_engine, old_argv
        if old_env is not None:
            os.environ["STONEWAVE_NOTES"] = old_env


def test_notas_el_doctor_dice_si_estan_activadas():
    import test_doctor as td
    old = os.environ.pop("STONEWAVE_NOTES", None)
    try:
        rc, out = td.run(td.healthy_db(), td.FakeRequests())
        assert rc == 0 and "Notas de Obsidian: apagadas" in out, out
        os.environ["STONEWAVE_NOTES"] = os.path.join(tempfile.mkdtemp(), "no-existe")
        rc, out = td.run(td.healthy_db(), td.FakeRequests())
        assert "Notas de Obsidian: activadas en" in out and "NO existe" in out, out
    finally:
        os.environ.pop("STONEWAVE_NOTES", None)
        if old is not None:
            os.environ["STONEWAVE_NOTES"] = old


if __name__ == "__main__":
    fallos = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as ex:
                fallos += 1
                print(f"FAIL  {name}: {type(ex).__name__}: {str(ex)[:600]}")
    print("\nTODO OK" if not fallos else f"\n{fallos} prueba(s) fallaron")
    sys.exit(1 if fallos else 0)
