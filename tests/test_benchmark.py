"""Pruebas del esqueleto de MemBench SIN Ollama (modo --fake).
Ejecutar:  python tests/test_benchmark.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mem_engine as me

spec = importlib.util.spec_from_file_location("benchmark_stones", os.path.join(ROOT, "scripts", "benchmark_stones.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@contextlib.contextmanager
def fake_thresholds():
    saved = {k: getattr(me, k) for k in bench.FAKE_THRESHOLDS}
    for k, v in bench.FAKE_THRESHOLDS.items():
        setattr(me, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(me, k, v)


def test_normalize_y_evaluate():
    assert bench.normalize("Colibrí ÁÉÍ") == "colibri aei"
    ok = bench.evaluate(["Colibri"], [], ["El proyecto Colibrí era una app"])
    assert ok["passed"] and ok["coverage"] == 1.0
    parcial = bench.evaluate(["UdeG", "constructora"], [], ["Estudié en la UdeG"])
    assert not parcial["passed"] and parcial["missed"] == ["constructora"] and parcial["coverage"] == 0.5
    prohibido = bench.evaluate(["Monterrey"], ["Guadalajara"], ["Vivo en Monterrey", "Vivo en Guadalajara"])
    assert not prohibido["passed"] and prohibido["forbidden_found"] == ["Guadalajara"]
    assert bench.evaluate([], [], [])["passed"]


def test_cargar_casos_jsonl():
    path = os.path.join(tempfile.mkdtemp(), "c.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "a", "facts": ["x"], "question": "q", "expected": ["x"]}) + "\n\n")
    assert [c["id"] for c in bench.load_cases(path)] == ["a"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "b", "facts": ["x"]}) + "\n")
    try:
        bench.load_cases(path)
        assert False, "debio fallar por campos faltantes"
    except ValueError as ex:
        assert "falta el campo" in str(ex)


def test_caso_completo_inyecta_sueña_consulta_compara():
    case = {"id": "t1", "category": "single", "background": False,
            "facts": ["Luis estudió ingeniería en computación en la universidad Lamar", "Luis trabaja en Neodata en Guadalajara"],
            "question": "qué estudió Luis en la universidad", "expected": ["Lamar"]}
    with fake_thresholds():
        for stones in (1, 5):
            r = bench.run_case(case, stones=stones, fake=True)
            assert r["passed"] and r["coverage"] == 1.0 and r["top"] and "ms" in r, r


def test_sotano_y_prohibidos():
    with fake_thresholds():
        sotano = {"id": "s1", "background": False, "facts": [{"text": "El helado de vainilla es muy rico en verano", "age_days": 200},
                                                              "Luis trabaja en Neodata en Guadalajara"],
                  "question": "helado de vainilla en verano", "expected": ["helado"]}
        assert bench.run_case(sotano, fake=True)["passed"]  # decae, baja al sotano y la consulta lo resucita
        prohibido = {"id": "p1", "background": False, "facts": ["Los gatos duermen mucho durante el día"],
                     "question": "gatos duermen mucho durante día", "expected": ["gatos"], "forbidden": ["duermen"]}
        r = bench.run_case(prohibido, fake=True)
        assert not r["passed"] and r["forbidden_found"] == ["duermen"], r


def test_casos_que_requieren_llm_se_omiten_en_fake():
    r = bench.run_case({"id": "c", "requires_llm": True, "facts": ["a"], "question": "q", "expected": ["a"]}, fake=True)
    assert r["skipped"] and r["passed"] is None


def test_expect_empty_pasa_solo_si_no_devuelve_nada():
    with fake_thresholds():
        hay = {"id": "e1", "background": False, "facts": ["Los gatos duermen mucho durante el día"],
               "question": "gatos duermen mucho durante día", "expected": [], "expect_empty": True}
        r = bench.run_case(hay, fake=True)
        assert not r["passed"] and r["coverage"] == 0.0 and r["forbidden_found"], r  # devolvio algo: falla
        vacio = {"id": "e2", "background": False, "facts": ["Los gatos duermen mucho durante el día"],
                 "question": "astronomía de agujeros negros", "expected": [], "expect_empty": True}
        r = bench.run_case(vacio, fake=True)
        assert r["passed"] and r["coverage"] == 1.0, r


def test_dataset_grande_es_valido():
    path = os.path.join(ROOT, "tests", "membench_cases.jsonl")
    cases = bench.load_cases(path)
    ids = [c["id"] for c in cases]
    assert len(cases) == 50 and len(set(ids)) == 50, (len(cases), len(set(ids)))
    cats = {}
    for c in cases:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
        assert isinstance(c["facts"], list) and c["facts"] and c["question"].strip(), c["id"]
        if c.get("expect_empty"):
            assert c["expected"] == [], c["id"]
        else:
            assert c["expected"], c["id"]
        if c["category"] == "contradiction":
            assert c.get("requires_llm") and c.get("forbidden"), c["id"]
        if c["category"] == "basement":
            assert any(isinstance(f, dict) and f.get("age_days") for f in c["facts"]), c["id"]
    assert cats == {"single": 14, "multi": 14, "contradiction": 10, "basement": 8, "no_answer": 4}, cats
    with fake_thresholds():  # corre de punta a punta sin romperse (con embeddings falsos el acierto no importa)
        rs = [bench.run_case(c, fake=True) for c in cases[:6]]
    assert all("error" not in r for r in rs), rs


def test_main_fake_de_punta_a_punta():
    old_argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = ["benchmark_stones.py", "--fake", "--stones", "1,5", "--no-background"]
        with contextlib.redirect_stdout(buf):
            rc = bench.main()
        out = buf.getvalue()
        assert rc == 0 and "MemBench: resumen" in out and "Omitidos" in out, out
        sys.argv = ["benchmark_stones.py", "--fake", "--stones", "5", "--no-background", "--min-pass", "1.0"]
        with contextlib.redirect_stdout(io.StringIO()):
            assert bench.main() == 1  # con embeddings falsos los casos de ejemplo no pasan todos
    finally:
        sys.argv = old_argv
    assert me.MIN_SIM == 0.18  # main restauro los umbrales reales


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
