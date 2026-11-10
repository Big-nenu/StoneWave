"""Pruebas de conclusiones derivadas de otras y del historial de conclusiones SIN Ollama (embeddings y modelos falsos).
Ejecutar:  python tests/test_historial.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import make_engine, fake_embedding, rows, F1, F2, F3, CONC
import mem_engine

CONC_NUEVA = CONC + " ahora"  # parecida (0.93) pero no igual: no cuenta como duplicado
CONC_B = CONC + " de ciencias"
DERIVADA = "Luis prefiere estudiar practicando en cualquier materia"


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def hipotesis_con_dos_recuerdos():
    e = make_engine()
    e._ask_llm_conclusion = lambda f: CONC
    e._ask_llm_support = lambda f, c: "RESPALDA"
    e.inject(F1)
    e.inject(F2)
    e.dream()
    return e


def confirmada(e, text, fact_ids):
    now = time.time()
    with sqlite3.connect(e.db_path) as c:
        cid = c.execute(
            "INSERT INTO conclusions (namespace, content, energy, embedding, status, support, against, created_at, last_checked, last_decay) "
            "VALUES ('default', ?, 0.8, ?, 'confirmada', 2, 0, ?, ?, ?)", (text, json.dumps(fake_embedding(text)), now, now, now)).lastrowid
        c.executemany("INSERT INTO conclusion_sources VALUES (?, ?)", [(cid, f) for f in fact_ids])
        c.commit()
    return cid


def dos_confirmadas():
    e = make_engine()
    e.inject(F1)
    e.inject(F2)
    e.inject(F3)
    ids = [i for i, _c, _en, _p in e.list_facts()]
    a, b = confirmada(e, CONC, ids[:2]), confirmada(e, CONC_B, ids[1:])
    e._ask_llm_derivation = lambda t: DERIVADA
    e._ask_llm_support = lambda f, c: "RESPALDA"
    return e, a, b


def links_resuelven(base):
    folders = [d for d in ("conclusiones", "recuerdos", "historial") if os.path.isdir(os.path.join(base, d))]
    names = {f[:-3] for d in folders for f in os.listdir(os.path.join(base, d))}
    for d in folders:
        for f in os.listdir(os.path.join(base, d)):
            for t in re.findall(r"\[\[([^\]|]+)\|", read(os.path.join(base, d, f))):
                assert t in names, (f, t)


# ----------------------------------------------------------------------
# Historial
# ----------------------------------------------------------------------
def test_historial_una_hipotesis_que_caduca_queda_en_el_historial():
    e = hipotesis_con_dos_recuerdos()
    assert len(e.list_conclusions()) == 1 and e.list_conclusion_history() == []
    with sqlite3.connect(e.db_path) as c:  # 60 dias sin respaldo: 0.3 * 0.97^60 < 0.1
        c.execute("UPDATE conclusions SET last_decay = last_decay - ?", (60 * 86400,))
        c.commit()
    out = e.dream()
    assert "1 caducadas" in out, out
    assert e.list_conclusions() == []
    h = e.list_conclusion_history()
    assert len(h) == 1 and h[0]["content"] == CONC and h[0]["reason"] == "caducada" and h[0]["status"] == "hipotesis", h
    assert len(h[0]["sources"]) == 2 and h[0]["ended"] and h[0]["orig_id"], h


def test_historial_una_conclusion_nueva_que_contradice_reemplaza_a_la_vieja():
    e = hipotesis_con_dos_recuerdos()
    viejo = e.list_conclusions()[0]["id"]
    e._ask_llm_conclusion = lambda f: CONC_NUEVA
    e._ask_llm_conflict = lambda a, b: "CONTRADICTORIO" if "ahora" in a + b else "COMPLEMENTARIO"
    e.inject(F3)
    out = e.dream()
    assert "1 reemplazadas" in out, out
    vivas = e.list_conclusions()
    assert [c["content"] for c in vivas] == [CONC_NUEVA], vivas
    h = e.list_conclusion_history()
    assert len(h) == 1 and h[0]["reason"] == "reemplazada" and h[0]["orig_id"] == viejo and h[0]["replaced_by"] == vivas[0]["id"], h


def test_historial_si_solo_se_parece_pero_no_contradice_no_reemplaza():
    e = hipotesis_con_dos_recuerdos()
    e._ask_llm_conclusion = lambda f: CONC_NUEVA  # el conflicto sigue en COMPLEMENTARIO
    e.inject(F3)
    e.dream()
    assert len(e.list_conclusions()) == 2 and e.list_conclusion_history() == []


def test_historial_olvidar_a_mano_no_deja_rastro_y_borrar_todo_lo_limpia():
    e = hipotesis_con_dos_recuerdos()
    cid = e.list_conclusions()[0]["id"]
    assert e.forget_conclusion(cid)["status"] == "deleted"
    assert e.list_conclusion_history() == []  # lo que mandas olvidar no se guarda
    e = hipotesis_con_dos_recuerdos()
    with sqlite3.connect(e.db_path) as c:
        c.execute("UPDATE conclusions SET last_decay = last_decay - ?", (60 * 86400,))
        c.commit()
    e.dream()
    assert len(e.list_conclusion_history()) == 1
    e.clear_db()
    assert e.list_conclusion_history() == []


def test_historial_tiene_tope():
    e = hipotesis_con_dos_recuerdos()
    old = mem_engine.HISTORY_MAX
    mem_engine.HISTORY_MAX = 3
    try:
        with sqlite3.connect(e.db_path) as c:
            for i in range(5):
                c.execute("INSERT INTO conclusions (namespace, content, energy, status, created_at) VALUES ('default', ?, 0.3, 'hipotesis', ?)", (f"vieja {i}", time.time()))
            for (cid,) in c.execute("SELECT id FROM conclusions WHERE content LIKE 'vieja%'").fetchall():
                e._retire_conclusion(c, cid, "caducada")
            c.commit()
    finally:
        mem_engine.HISTORY_MAX = old
    assert [h["content"] for h in e.list_conclusion_history()] == ["vieja 4", "vieja 3", "vieja 2"]


# ----------------------------------------------------------------------
# Conclusiones derivadas de otras
# ----------------------------------------------------------------------
def test_derivar_una_conclusion_de_dos_confirmadas():
    e, a, b = dos_confirmadas()
    assert e._derive_conclusions("default") == 1
    nueva = [c for c in e.list_conclusions() if c["content"] == DERIVADA]
    assert len(nueva) == 1 and nueva[0]["status"] == "hipotesis" and nueva[0]["parents"] == sorted([a, b]), nueva
    assert abs(nueva[0]["energy"] - 0.3) < 1e-6
    assert len(nueva[0]["sources"]) == 3, nueva  # la union de los recuerdos de sus padres
    assert rows(e.db_path, "SELECT COUNT(*) FROM conclusions WHERE derived=1") == [(2,)]
    assert e._derive_conclusions("default") == 0  # ya se intento con ese grupo: no se repite
    assert len(e.list_conclusions()) == 3


def test_derivar_solo_de_confirmadas_y_necesita_al_menos_dos():
    e = make_engine()
    e.inject(F1)
    e.inject(F2)
    ids = [i for i, _c, _en, _p in e.list_facts()]
    confirmada(e, CONC, ids)
    e._ask_llm_derivation = lambda t: DERIVADA
    assert e._derive_conclusions("default") == 0  # una sola confirmada: no hay con quien
    with sqlite3.connect(e.db_path) as c:  # una hipotesis no cuenta como base
        c.execute("INSERT INTO conclusions (namespace, content, energy, embedding, status, created_at, last_checked) VALUES ('default', ?, 0.3, ?, 'hipotesis', 1, 1)",
                  (CONC_B, json.dumps(fake_embedding(CONC_B))))
        c.commit()
    assert e._derive_conclusions("default") == 0
    assert rows(e.db_path, "SELECT COUNT(*) FROM conclusions WHERE derived=1") == [(0,)]  # sin intento: se reintenta despues


def test_derivar_exige_verificacion_y_modelo_caido_no_marca_nada():
    e, a, b = dos_confirmadas()
    e._ask_llm_support = lambda f, c: "NEUTRO"
    assert e._derive_conclusions("default") == 0 and len(e.list_conclusions()) == 2
    e, a, b = dos_confirmadas()
    e._ask_llm_derivation = lambda t: None  # el modelo no responde
    assert e._derive_conclusions("default") == 0
    assert rows(e.db_path, "SELECT COUNT(*) FROM conclusions WHERE derived=1") == [(0,)]


def test_derivar_no_repite_a_un_padre_ni_hace_ciclos():
    e, a, b = dos_confirmadas()
    e._ask_llm_derivation = lambda t: CONC  # dice lo mismo que un padre
    assert e._derive_conclusions("default") == 0
    assert rows(e.db_path, "SELECT COUNT(*) FROM conclusion_parents") == [(0,)]


def test_derivar_el_sueno_lo_hace_y_lo_cuenta():
    e, a, b = dos_confirmadas()
    out = e.dream()
    assert "1 derivadas de otras" in out, out
    assert rows(e.db_path, "SELECT COUNT(*) FROM conclusion_parents") == [(2,)]


def test_derivar_si_el_padre_caduca_la_hija_sigue_apuntando_en_el_historial():
    e, a, b = dos_confirmadas()
    e._derive_conclusions("default")
    with sqlite3.connect(e.db_path) as c:
        e._retire_conclusion(c, a, "caducada")
        c.commit()
    assert rows(e.db_path, f"SELECT COUNT(*) FROM conclusion_parents WHERE parent_id={a}") == [(1,)]
    hija = [x for x in e.list_conclusions() if x["content"] == DERIVADA][0]
    assert a in hija["parents"]


# ----------------------------------------------------------------------
# Notas de Obsidian
# ----------------------------------------------------------------------
def test_notas_derivada_enlaza_a_sus_padres_y_ellos_a_ella():
    import notes
    e, a, b = dos_confirmadas()
    e._derive_conclusions("default")
    base = tempfile.mkdtemp()
    r = e.export_notes(base_dir=base)
    assert r["status"] == "ok" and r["conclusions"] == 3 and r["history"] == 0, r
    assert not os.path.isdir(os.path.join(base, "historial"))  # sin historial no hay carpeta
    hija = read(os.path.join(base, "conclusiones", notes.note_name("conclusion", DERIVADA) + ".md"))
    padre = read(os.path.join(base, "conclusiones", notes.note_name("conclusion", CONC) + ".md"))
    assert "## Deriva de" in hija and notes.note_name("conclusion", CONC) in hija and notes.note_name("conclusion", CONC_B) in hija
    assert "## De aquí nacieron" in padre and notes.note_name("conclusion", DERIVADA) in padre
    assert notes.note_name("conclusion", DERIVADA) not in padre.split("## Relacionada con")[1]  # ya salio como 'De aqui nacieron'
    links_resuelven(base)


def test_notas_historial_enlaza_la_reemplazada_con_la_nueva():
    import notes
    e = hipotesis_con_dos_recuerdos()
    e._ask_llm_conclusion = lambda f: CONC_NUEVA
    e._ask_llm_conflict = lambda a, b: "CONTRADICTORIO" if "ahora" in a + b else "COMPLEMENTARIO"
    e.inject(F3)
    e.dream()
    base = tempfile.mkdtemp()
    r = e.export_notes(base_dir=base)
    assert r["status"] == "ok" and r["history"] == 1 and r["conclusions"] == 1, r
    files = os.listdir(os.path.join(base, "historial"))
    assert len(files) == 1
    vieja = read(os.path.join(base, "historial", files[0]))
    assert "estado: \"reemplazada\"" in vieja and "historial" in vieja and "## Reemplazada por" in vieja, vieja
    assert notes.note_name("conclusion", CONC_NUEVA) in vieja
    nueva = read(os.path.join(base, "conclusiones", notes.note_name("conclusion", CONC_NUEVA) + ".md"))
    assert "## Reemplazó a" in nueva and files[0][:-3] in nueva, nueva
    mem = [read(os.path.join(base, "recuerdos", f)) for f in os.listdir(os.path.join(base, "recuerdos"))]
    assert any(files[0][:-3] in m for m in mem)  # el recuerdo tambien enlaza a la que ayudo a formar, aunque ya no cuente
    links_resuelven(base)
    again = e.export_notes(base_dir=base)
    assert again["written"] == 0 and again["deleted"] == 0, again  # exportar de nuevo no cambia nada


def test_notas_el_historial_no_pisa_notas_tuyas_y_se_limpia_solo():
    import notes
    e = hipotesis_con_dos_recuerdos()
    with sqlite3.connect(e.db_path) as c:
        c.execute("UPDATE conclusions SET last_decay = last_decay - ?", (60 * 86400,))
        c.commit()
    e.dream()
    base = tempfile.mkdtemp()
    e.export_notes(base_dir=base)
    folder = os.path.join(base, "historial")
    mia = os.path.join(folder, "mi nota.md")
    with open(mia, "w", encoding="utf-8") as f:
        f.write("# mia\n")
    with sqlite3.connect(e.db_path) as c:
        c.execute("DELETE FROM conclusion_history")
        c.commit()
    r = e.export_notes(base_dir=base)
    assert r["deleted"] >= 1 and os.path.exists(mia), r  # se va la nota que ya no corresponde; la tuya se queda
    assert [f for f in os.listdir(folder) if f != "mi nota.md"] == []


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
