"""Pruebas del motor SIN Ollama: usa embeddings falsos (bolsa de palabras) y LLM falsos.
Ejecutar:  python tests/test_engine.py
"""
import os
import re
import sys
import json
import time
import sqlite3
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mem_engine import MemHolograficEngine
import mem_engine

mem_engine.CENTER_PRIOR = 10 ** 9  # sin centrado: los embeddings falsos no lo necesitan
# Umbrales para similitud coseno cruda (los embeddings falsos no estan centrados)
mem_engine.WEAK_MATCH, mem_engine.MIN_SIM, mem_engine.WAVE_MIN_SIM = 0.65, 0.5, 0.45
mem_engine.DUPLICATE_SIM, mem_engine.CONFLICT_SIM, mem_engine.RELATED_SIM = 0.95, 0.8, 0.6


def fake_embedding(text):
    v = [0.0] * 256
    for w in re.findall(r"\w+", text.lower()):
        if len(w) > 2:
            v[zlib.crc32(w.encode()) % 256] += 1.0
    return v


def make_engine(db=None, verdict="COMPLEMENTARIO"):
    db = db or os.path.join(tempfile.mkdtemp(), "t.db")
    e = MemHolograficEngine(db_path=db)
    e.get_embedding = fake_embedding
    e.get_embeddings = lambda texts: [fake_embedding(t) for t in texts]
    e.classify_topics = lambda t: ["otro"]
    e._ask_llm_conflict = lambda a, b: verdict
    return e


def rows(db, sql):
    with sqlite3.connect(db) as c:
        return c.execute(sql).fetchall()


def age(db, days, where="1=1"):
    with sqlite3.connect(db) as c:
        c.execute(f"UPDATE facts SET last_used=? WHERE {where}", (time.time() - days * 86400,))
        c.commit()


def test_inject_nuevo_y_refuerzo():
    e = make_engine()
    t = "Luis estudió ingeniería en computación en la universidad Lamar"
    assert "NUEVA" in e.inject(t)
    assert "REFUERZO" in e.inject(t)
    r = rows(e.db_path, "SELECT energy FROM facts")
    assert len(r) == 1 and abs(r[0][0] - 0.7) < 1e-6, r


def test_fijo_no_decae():
    e = make_engine()
    e.inject("Me llamo Luis Eduardo y vivo en Guadalajara", pinned=True)
    e.inject("El helado de vainilla es muy rico en verano", energy=0.5)
    age(e.db_path, 200)
    assert e.decay() == 1
    assert e.get_all_facts() == ["Me llamo Luis Eduardo y vivo en Guadalajara"]
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 1


def test_sotano_resucita():
    e = make_engine()
    e.inject("El helado de vainilla es muy rico en verano")
    e.inject("Luis trabaja en Neodata en Guadalajara")
    age(e.db_path, 200, "content LIKE '%helado%'")
    e.decay()
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 1
    res = e.query("helado de vainilla en verano")
    assert isinstance(res, list) and "helado" in res[0][3], res
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 0
    assert len(rows(e.db_path, "SELECT * FROM facts")) == 2


def test_sueno_contradiccion_archiva_el_viejo():
    e = make_engine(verdict="CONTRADICTORIO")
    e.inject("mi perro pequeño juega en el jardín todas las tardes")
    e.inject("mi perro pequeño juega en el jardín todas las mañanas")
    e.dream()
    activos = [r[0] for r in rows(e.db_path, "SELECT content FROM facts")]
    sotano = [r[0] for r in rows(e.db_path, "SELECT content FROM archived_facts")]
    assert activos == ["mi perro pequeño juega en el jardín todas las mañanas"], activos
    assert sotano == ["mi perro pequeño juega en el jardín todas las tardes"], sotano


def test_sueno_similares_no_se_fusionan():
    e = make_engine(verdict="COMPLEMENTARIO")
    a = "mi perro pequeño juega en el jardín todas las tardes"
    b = "mi perro pequeño juega en el jardín todas las mañanas"
    e.inject(a)
    e.inject(b)
    e.dream()
    assert sorted(e.get_all_facts()) == sorted([a, b])  # nada se reescribe ni se pierde
    assert [r[0] for r in rows(e.db_path, "SELECT kind FROM links")] == ["similar"]


def test_ondas_encuentran_el_recuerdo():
    e = make_engine()
    A = "Luis estudió ingeniería en computación en la universidad Lamar"
    e.inject(A)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    e.inject("Los gatos duermen mucho durante el día")
    res = e.query("qué estudió Luis en la universidad")
    assert isinstance(res, list) and res[0][3] == A, res
    assert e.query("astronomía de agujeros negros") == "No related memories found."


def test_cascada_por_enlaces():
    e = make_engine()
    A = "Luis estudió ingeniería en computación en la universidad Lamar"
    A2 = "Luis estudió ingeniería computación y se graduó en 2013"
    e.inject(A)
    e.inject(A2)
    e.inject("Los gatos duermen mucho durante el día")
    e.dream()
    assert [r[0] for r in rows(e.db_path, "SELECT kind FROM links")] == ["relacionado"]
    res = e.query("estudió luis universidad lamar")
    textos = [r[3] for r in res]
    assert textos[0] == A and A2 in textos, textos


def test_cache_se_actualiza():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    e1, e2 = make_engine(db), make_engine(db)
    e1.inject("Luis trabaja en Neodata en Guadalajara")
    assert e1.query("trabaja Neodata Guadalajara Luis")[0][3].startswith("Luis trabaja")
    e1.inject("Los gatos duermen mucho durante el día")           # misma instancia
    assert "gatos" in e1.query("gatos duermen mucho durante día")[0][3]
    e2.inject("El telescopio de mi vecino tiene una montura ecuatorial")   # otro proceso
    assert "telescopio" in e1.query("telescopio vecino montura ecuatorial")[0][3]


def test_sueno_automatico_toca_cuando_pasan_las_horas():
    e = make_engine()
    assert e.dream_due() is False               # sin recuerdos no hay nada que ordenar
    e.inject("Luis trabaja en Neodata en Guadalajara")
    assert e.dream_due() is True                # nunca ha soñado
    e.dream()
    assert e.dream_due() is False and e.hours_since_dream() < 0.01
    with sqlite3.connect(e.db_path) as c:
        c.execute("UPDATE meta SET value=? WHERE key='last_dream:default'", (str(time.time() - 13 * 3600),))
        c.commit()
    assert e.dream_due() is True                # pasaron 13 h (el limite son 12)
    assert e.dream_due(hours=24) is False


def _ids(e, namespace="default"):
    return {c: i for i, c, _en, _p in e.list_facts(namespace)}


def test_forget_por_id_y_por_texto():
    e = make_engine()
    A, B = "Luis trabaja en Neodata en Guadalajara", "Los gatos duermen mucho durante el día"
    e.inject(A)
    e.inject(B)
    r = e.forget(_ids(e)[B])
    assert r["status"] == "deleted" and r["content"] == B, r
    assert e.get_all_facts() == [A]
    r = e.forget("  LUIS trabaja   en neodata en Guadalajara ")  # igual sin importar mayusculas ni espacios
    assert r["status"] == "deleted", r
    assert e.get_all_facts() == []
    assert e.forget(999)["status"] == "not_found"
    assert e.forget("")["status"] == "error"
    assert e.forget(None)["status"] == "error"


def test_forget_limpia_temas_y_enlaces():
    e = make_engine()
    e.inject("Luis estudió ingeniería en computación en la universidad Lamar")
    e.inject("Luis estudió ingeniería computación y se graduó en 2013")
    e.dream()
    assert rows(e.db_path, "SELECT * FROM links") and rows(e.db_path, "SELECT * FROM fact_topics")
    fid = list(_ids(e).values())[0]
    assert e.forget(str(fid))["status"] == "deleted"  # un numero como texto se toma como ID
    assert rows(e.db_path, "SELECT * FROM links") == []
    assert len(rows(e.db_path, "SELECT * FROM fact_topics")) == 1


def test_forget_por_similitud_y_sugerencias():
    e = make_engine()
    A = "mi perro pequeño juega en el jardín todas las tardes cuando llega mamá a casa del trabajo hoy"
    e.inject(A)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    r = e.forget("mi perro pequeño juega en el jardín todas las tardes cuando llega mamá a casa del trabajo")  # casi igual
    assert r["status"] == "deleted" and r["content"] == A, r
    e.inject("Luis trabaja en Neodata en Guadalajara ciudad")
    r = e.forget("trabaja Luis Neodata")  # parecido pero no casi igual: no borra, sugiere
    assert r["status"] == "not_found" and r["suggestions"], r
    assert len(e.get_all_facts()) == 2  # nada se borro


def test_forget_ambiguo_pide_id():
    e = make_engine()
    A = "Luis trabaja en Neodata en Guadalajara"
    e.inject(A)
    with sqlite3.connect(e.db_path) as c:  # un duplicado exacto "heredado" (inject normal lo habria reforzado)
        c.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned) "
                  "SELECT namespace, content, energy, embedding, last_used, pinned FROM facts")
        c.commit()
    r = e.forget(A)
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2, r
    assert len(rows(e.db_path, "SELECT * FROM facts")) == 2  # no se borro nada
    assert e.forget(r["candidates"][0][0])["status"] == "deleted"
    assert len(rows(e.db_path, "SELECT * FROM facts")) == 1


def test_forget_tambien_borra_del_sotano():
    e = make_engine()
    H = "El helado de vainilla es muy rico en verano"
    e.inject(H)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    age(e.db_path, 200, "content LIKE '%helado%'")
    e.decay()
    assert len(rows(e.db_path, "SELECT * FROM archived_facts")) == 1
    r = e.forget(H)  # solo existe en el sotano
    assert r["status"] == "deleted" and r["where"] == "sotano", r
    assert e.query("helado de vainilla en verano") == "No related memories found."  # ya no resucita
    # si esta activo y tambien hay copia en el sotano, ambas se van
    e.inject(H)
    with sqlite3.connect(e.db_path) as c:
        c.execute("INSERT INTO archived_facts (namespace, content, energy, embedding, reason) SELECT namespace, content, energy, embedding, 'desuso' FROM facts WHERE content=?", (H,))
        c.commit()
    r = e.forget(_ids(e)[H])
    assert r["status"] == "deleted" and r["archived_removed"] == 1, r
    assert rows(e.db_path, "SELECT * FROM archived_facts") == []


def test_forget_respeta_el_namespace():
    e = make_engine()
    e.inject("El telescopio de mi vecino tiene montura ecuatorial", namespace="astro")
    fid = _ids(e, "astro")["El telescopio de mi vecino tiene montura ecuatorial"]
    assert e.forget(fid)["status"] == "not_found"  # el ID existe, pero en otro namespace
    assert e.forget(fid, namespace="astro")["status"] == "deleted"


def test_update_memory_corrige_y_conserva_energia_y_fijo():
    e = make_engine()
    old = "Vivo en Guadalajara con mis papás"
    new = "Vivo en Monterrey con mi pareja"
    e.inject(old, pinned=True, energy=0.8)
    e.inject("Los gatos duermen mucho durante el día")
    fid = _ids(e)[old]
    r = e.update_memory(fid, "  " + new + "  ")
    assert r["status"] == "updated" and r["old"] == old and r["new"] == new, r
    assert new in e.get_all_facts() and old not in e.get_all_facts()
    assert rows(e.db_path, f"SELECT pinned, energy FROM facts WHERE id={fid}") == [(1, 0.8)]
    res = e.query("vivo monterrey pareja")  # el vector cambio: la consulta encuentra el texto nuevo
    assert isinstance(res, list) and res[0][1] == fid and res[0][3] == new, res
    assert e.update_memory(999, "x")["status"] == "not_found"
    assert e.update_memory(fid, "   ")["status"] == "error"
    assert e.update_memory("abc", "x")["status"] == "error"
    assert e.update_memory(fid, new, namespace="otro")["status"] == "not_found"


def test_update_memory_avisa_si_queda_casi_igual_a_otro():
    e = make_engine()
    A = "mi perro pequeño juega en el jardín todas las tardes cuando llega mamá a casa del trabajo hoy"
    e.inject(A)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    id_a = _ids(e)[A]
    fid = _ids(e)["Luis trabaja en Neodata en Guadalajara"]
    r = e.update_memory(fid, A + " ")
    assert r["status"] == "updated" and r["similar_to"] == id_a, r


def test_export_e_import_jsonl_ida_y_vuelta():
    src = make_engine()
    src.inject("Me llamo Luis Eduardo y vivo en Guadalajara", pinned=True, energy=0.9)
    src.inject("El telescopio de mi vecino tiene montura ecuatorial", namespace="astro")
    path = os.path.join(tempfile.mkdtemp(), "backup.jsonl")
    assert src.export_memories(path) == 2
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f]
    assert all(set(l) == {"content", "namespace", "fijo", "energy", "created_at", "source"} for l in lines), lines
    by_ns = {l["namespace"]: l for l in lines}
    assert by_ns["default"]["fijo"] is True and abs(by_ns["default"]["energy"] - 0.9) < 1e-6
    assert by_ns["astro"]["created_at"].endswith("Z")
    assert src.export_memories(path + ".astro", namespace="astro") == 1

    dst = make_engine()
    s = dst.import_memories(path)
    assert s["new"] == 2 and s["reinforced"] == 0 and not s["invalid"] and not s["aborted"], s
    assert dst.get_all_facts("default") == ["Me llamo Luis Eduardo y vivo en Guadalajara"]
    assert dst.get_all_facts("astro") == ["El telescopio de mi vecino tiene montura ecuatorial"]
    assert rows(dst.db_path, "SELECT pinned FROM facts WHERE namespace='default'") == [(1,)]
    assert len(s["dream"]) == 2 and len(rows(dst.db_path, "SELECT key FROM meta WHERE key LIKE 'last_dream:%'")) == 2

    s2 = dst.import_memories(path)  # importar dos veces no duplica: refuerza
    assert s2["new"] == 0 and s2["reinforced"] == 2 and s2["dream"] == [], s2
    assert len(rows(dst.db_path, "SELECT * FROM facts")) == 2
    s3 = make_engine().import_memories(path, run_dream=False)
    assert s3["new"] == 2 and s3["dream"] == []


def test_import_valida_lineas_y_respeta_campos():
    path = os.path.join(tempfile.mkdtemp(), "mix.jsonl")
    with open(path, "w", encoding="utf-8-sig") as f:  # con BOM, como lo deja el Bloc de notas
        f.write(json.dumps({"content": "Luis trabaja en Neodata", "created_at": "2026-01-01T00:00:00Z", "source": "claude", "fijo": True}) + "\n")
        f.write("esto no es json\n")
        f.write(json.dumps({"namespace": "x"}) + "\n")
        f.write("[1, 2]\n")
        f.write(json.dumps({"content": "Dato con energia mala", "energy": "mucha"}) + "\n\n")
        f.write(json.dumps({"content": "Los gatos duermen mucho durante el día", "energy": 7}) + "\n")
    e = make_engine()
    s = e.import_memories(path, namespace="otro")  # el namespace dado manda sobre el del archivo
    assert s["new"] == 2 and [l for l, _ in s["invalid"]] == [2, 3, 4, 5], s
    assert sorted(e.get_all_facts("otro")) == sorted(["Luis trabaja en Neodata", "Los gatos duermen mucho durante el día"])
    assert rows(e.db_path, "SELECT created_at, source, pinned FROM facts WHERE content LIKE 'Luis%'") == [(1767225600.0, "claude", 1)]
    assert rows(e.db_path, "SELECT energy, source FROM facts WHERE content LIKE 'Los gatos%'") == [(1.0, "import")]


def test_import_se_detiene_si_ollama_no_responde():
    e = make_engine()
    e.get_embedding = lambda t: None
    s = e.import_records([{"content": f"recuerdo {i}"} for i in range(6)])
    assert s["aborted"] and s["errors"] == 3 and s["new"] == 0 and s["dream"] == [], s


def test_bd_vieja_sin_created_at_se_migra():
    db = os.path.join(tempfile.mkdtemp(), "vieja.db")
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT DEFAULT 'default', content TEXT, energy REAL, embedding TEXT, last_used REAL, pinned INTEGER DEFAULT 0)")
        c.execute("INSERT INTO facts (namespace, content, energy, embedding, last_used, pinned) VALUES ('default', 'dato viejo', 0.5, ?, 1767225600.0, 0)", (json.dumps(fake_embedding("dato viejo")),))
        c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")  # esquema ya al dia: que no intente recalcular con Ollama
        c.execute("INSERT INTO meta VALUES ('embed_scheme', ?)", (f"{mem_engine.EMBEDDING_MODEL}|{mem_engine.QUERY_PREFIX}|{mem_engine.DOC_PREFIX}",))
        c.commit()
    e = make_engine(db)
    path = os.path.join(tempfile.mkdtemp(), "v.jsonl")
    assert e.export_memories(path) == 1
    with open(path, encoding="utf-8") as f:
        rec = json.loads(f.readline())
    assert rec["created_at"] == "2026-01-01T00:00:00Z" and rec["source"] == "stonewave", rec


def _mcp_sample(folder):
    p = os.path.join(folder, "memory.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "entity", "name": "Luis", "entityType": "person", "observations": ["Works at Neodata", "Luis lives in Guadalajara", "Works at Neodata"]}) + "\n")
        f.write(json.dumps({"type": "entity", "name": "Neodata", "entityType": "company", "observations": []}) + "\n")
        f.write(json.dumps({"type": "relation", "from": "Luis", "to": "Atom-Fusion", "relationType": "develops"}) + "\n")
        f.write(json.dumps({"type": "relation", "from": "Luis", "to": "Lamar", "relationType": "studiedAt"}) + "\n")
        f.write(json.dumps({"type": "relation", "from": "Neodata", "to": "Guadalajara", "relationType": "located_in"}) + "\n")
        f.write("basura\n")
    return p


def test_parser_mcp_memory_convierte_e_importa():
    from parsers.mcp_parser import load_records, convert
    folder = tempfile.mkdtemp()
    src = _mcp_sample(folder)
    recs, stats = load_records(src, namespace="trabajo", pinned_entities=["luis"])
    contents = [r["content"] for r in recs]
    assert contents == ["Luis: Works at Neodata", "Luis lives in Guadalajara", "Luis develops Atom-Fusion",
                        "Luis studied at Lamar", "Neodata located in Guadalajara"], contents
    assert [r["fijo"] for r in recs] == [True, True, True, True, False]
    assert all(r["namespace"] == "trabajo" and r["source"] == "mcp-memory" for r in recs)
    assert stats["duplicates"] == 1 and stats["invalid_lines"] == 1 and stats["empty_entities"] == 1, stats
    out = os.path.join(folder, "canonico.jsonl")
    assert convert(src, out, namespace="trabajo")["records"] == 5
    e = make_engine()
    s = e.import_memories(out)
    assert s["new"] == 5 and not s["invalid"], s
    assert len(e.get_all_facts("trabajo")) == 5


def test_cli_import_y_export():
    import contextlib
    import io
    import mem_cli
    db = os.path.join(tempfile.mkdtemp(), "cli.db")
    old_engine, old_argv = mem_cli.StoneWaveEngine, sys.argv
    mem_cli.StoneWaveEngine = lambda: make_engine(db)

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
        folder = tempfile.mkdtemp()
        mcp = _mcp_sample(folder)
        code, out = run("import", mcp, "--from", "mcp-memory", "-n", "trabajo")
        assert code == 0 and "Nuevos: 5" in out and "Sleeptime" in out, out
        backup = os.path.join(folder, "backup.jsonl")
        code, out = run("export", backup)
        assert code == 0 and "5" in out and os.path.exists(backup), out
        code, out = run("import", backup, "--no-dream")
        assert code == 0 and "Reforzados: 5" in out and "Sleeptime" not in out, out
        code, out = run("import", os.path.join(folder, "no_existe.jsonl"))
        assert code == 1 and "No se encontró" in out, out
        code, out = run("import")
        assert code == 1, out
    finally:
        mem_cli.StoneWaveEngine, sys.argv = old_engine, old_argv


def test_papelera_guarda_lo_olvidado_y_lo_encuentra():
    e = make_engine()
    C = "Mi color favorito es el azul marino"
    e.inject(C)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    assert e.forget(_ids(e)[C])["status"] == "deleted"
    assert e.query("color favorito azul marino") == "No related memories found."  # ya no esta vigente
    hits = e.query_trash("mi color favorito")
    assert hits and hits[0]["content"] == C and hits[0]["reason"] == "olvidado" and hits[0]["current"] is None, hits
    assert e.query_trash("mi color favorito", namespace="otro") == []


def test_papelera_reemplazado_apunta_al_valor_actual():
    e = make_engine()
    old, new = "Vivo en Guadalajara con mis papás", "Vivo en Monterrey con mi pareja"
    e.inject(old)
    fid = _ids(e)[old]
    assert e.update_memory(fid, new)["status"] == "updated"
    hits = e.query_trash("vivo guadalajara papás")
    assert hits and hits[0]["reason"] == "reemplazado" and hits[0]["current"] == new, hits
    e.update_memory(fid, "  " + new.upper() + " ")  # mismo texto escrito distinto: no ensucia la papelera
    assert len(e.list_trash()) == 1


def test_papelera_sotano_y_borrado_definitivo():
    e = make_engine()
    H = "El helado de vainilla es muy rico en verano"
    G = "Los gatos duermen mucho durante el día"
    e.inject(H)
    e.inject("Luis trabaja en Neodata en Guadalajara")
    age(e.db_path, 200, "content LIKE '%helado%'")
    e.decay()
    assert e.forget(H)["where"] == "sotano"  # solo estaba en el sotano: tambien va a la papelera
    assert [t[1] for t in e.list_trash()] == [H]
    e.inject(G)
    assert e.forget(_ids(e)[G])["status"] == "deleted"
    assert len(e.list_trash()) == 2
    assert e.purge_trash(trash_id=e.list_trash()[0][0]) == 1
    assert len(e.list_trash()) == 1
    assert e.purge_trash() == 1 and e.list_trash() == []
    e.inject(H)
    e.forget(_ids(e)[H])
    assert len(e.list_trash()) == 1
    e.clear_db()  # borrar todo tambien vacia la papelera
    assert e.list_trash() == []


if __name__ == "__main__":
    fallos = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as ex:
                fallos += 1
                print(f"FAIL  {name}: {type(ex).__name__}: {ex}")
    print("\nTODO OK" if not fallos else f"\n{fallos} prueba(s) fallaron")
    sys.exit(1 if fallos else 0)
