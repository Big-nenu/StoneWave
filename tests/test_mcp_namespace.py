"""Pruebas del servidor MCP: cada herramienta acepta un namespace opcional (un proyecto). SIN Ollama.
Ejecutar:  python tests/test_mcp_namespace.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["STONEWAVE_DB"] = os.path.join(tempfile.mkdtemp(), "mcp.db")  # que no toque tu memoria real
for var in ("STONEWAVE_NAMESPACE", "MEMHOLOGRAFIC_NAMESPACE"):
    os.environ.pop(var, None)
from test_engine import make_engine
import mcp_server as srv

TEL = "El telescopio de mi vecino tiene montura ecuatorial"
NEO = "Luis trabaja en Neodata en Guadalajara"


def fresh():
    srv.engine = make_engine()
    return srv.engine


def test_helper_ns():
    assert srv._ns() == srv.NS and srv._ns(None) == srv.NS and srv._ns("   ") == srv.NS
    assert srv._ns("  proyecto-12 ") == "proyecto-12"


def test_sin_namespace_usa_el_de_siempre():
    e = fresh()
    srv.inject_memory(NEO)
    assert [f[1] for f in e.list_facts(srv.NS)] == [NEO]


def test_el_proyecto_queda_aislado_del_default():
    e = fresh()
    srv.inject_memory(NEO)
    srv.inject_memory(TEL, namespace="proyecto-12")
    assert [f[1] for f in e.list_facts("proyecto-12")] == [TEL]
    assert "telescopio" in srv.query_memory("telescopio vecino montura ecuatorial", namespace="proyecto-12")
    assert "telescopio" not in srv.query_memory("telescopio vecino montura ecuatorial")  # el chat normal no lo ve
    assert "telescopio" not in srv.list_memories() and "telescopio" in srv.list_memories(namespace="proyecto-12")
    assert "telescopio" in srv.get_context(namespace="proyecto-12") and "telescopio" not in srv.get_context()


def test_olvidar_y_corregir_respetan_el_namespace():
    e = fresh()
    srv.inject_memory(TEL, namespace="proyecto-12")
    fid = e.list_facts("proyecto-12")[0][0]
    assert "No hay" in srv.forget_memory(fact_id=fid)  # sin namespace busca en default: no lo encuentra
    assert "No hay" in srv.update_memory(fid, "otra cosa")
    assert srv.forget_memory(fact_id=fid, namespace="proyecto-12").startswith("Olvidado")
    assert e.list_facts("proyecto-12") == []


def test_clear_memory_solo_borra_la_etiqueta_indicada():
    e = fresh()
    srv.inject_memory(NEO)
    srv.inject_memory(TEL, namespace="proyecto-12")
    assert "proyecto-12" in srv.clear_memory(confirm=True, namespace="proyecto-12")
    assert e.list_facts("proyecto-12") == [] and len(e.list_facts(srv.NS)) == 1


def test_el_sueno_se_hace_por_etiqueta_y_se_listan_las_etiquetas():
    e = fresh()
    srv.inject_memory(NEO)
    srv.inject_memory(TEL, namespace="proyecto-12")
    assert e.list_namespaces() == sorted([srv.NS, "proyecto-12"])
    assert "proyecto-12" in srv.force_dream(namespace="proyecto-12")
    assert e.hours_since_dream("proyecto-12") is not None and e.hours_since_dream(srv.NS) is None


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
