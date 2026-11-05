"""Pruebas de `setup` SIN Ollama: se simula Ollama y la descarga de modelos.
Ejecutar:  python tests/test_setup.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mem_engine as me
import setup_wizard as sw

ALL = [me.EMBEDDING_MODEL, me.LLM_MODEL, me.SYNTHESIS_MODEL]
sw._have = lambda module: True  # no depender de lo instalado en la maquina de pruebas


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, models=None, down=False):
        self.models = ALL if models is None else models
        self.down = down

    def get(self, url, **kw):
        if self.down:
            raise ConnectionError("Ollama apagado")
        return FakeResp({"models": [{"name": m} for m in self.models]})


class Done:
    def __init__(self, code):
        self.returncode = code


def run(http, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.run_setup(http=http, **kw)
    return rc, buf.getvalue()


def test_todo_listo_imprime_la_config_con_rutas_reales():
    rc, out = run(FakeHttp(), which=lambda n: None)
    cfg = sw.build_config()["mcpServers"]["stonewave"]
    assert rc == 0 and "Todo listo" in out, out
    assert cfg["command"] == sys.executable and cfg["args"][0].endswith("mcp_server.py"), cfg
    assert '"mcpServers"' in out and "claude mcp add stonewave" in out and "stonewave-memory" in out, out


def test_ollama_apagado_es_problema_y_dice_como_arrancarlo():
    rc, out = run(FakeHttp(down=True), which=lambda n: None)
    assert rc == 1 and "[XX] Ollama no responde" in out and "ollama" in out.lower(), out
    assert '"mcpServers"' in out  # aun asi imprime la configuracion


def test_modelo_faltante_sin_pull_da_el_comando():
    rc, out = run(FakeHttp(models=[me.EMBEDDING_MODEL, me.SYNTHESIS_MODEL]), which=lambda n: "ollama")
    assert rc == 1 and f"$ ollama pull {me.LLM_MODEL}" in out, out


def test_pull_descarga_lo_que_falta():
    calls = []
    rc, out = run(FakeHttp(models=[me.EMBEDDING_MODEL, me.SYNTHESIS_MODEL]), pull=True,
                  which=lambda n: "ollama", runner=lambda cmd, **kw: calls.append(cmd) or Done(0))
    assert rc == 0 and calls == [["ollama", "pull", me.LLM_MODEL]], (rc, calls, out)


def test_pull_que_falla_sigue_siendo_problema():
    rc, out = run(FakeHttp(models=[me.EMBEDDING_MODEL, me.SYNTHESIS_MODEL]), pull=True,
                  which=lambda n: "ollama", runner=lambda cmd, **kw: Done(1))
    assert rc == 1 and "no se pudo descargar" in out, out


def test_modelo_de_reflexion_faltante_es_solo_aviso():
    rc, out = run(FakeHttp(models=[me.EMBEDDING_MODEL, me.LLM_MODEL]), which=lambda n: None)
    assert rc == 0 and "[!!] Reflexion" in out, out


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
