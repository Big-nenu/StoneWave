"""recent_notes: lee las notas de Obsidian mas recientes SIN modificarlas (datos sinteticos, sin Ollama).
Ejecutar:  python tests/test_recent_notes.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import make_engine
import notes

NOTE = ('---\ntipo: "conclusion"\n---\n# Titulo de prueba\n\n' + notes._WARNING +
        '\n\n## Viene de\n- [[recuerdo (abc123)|Texto del recuerdo]]\n')


def make_notes(root):
    for folder, name, age in (("conclusiones", "vieja", 300), ("conclusiones", "nueva", 0), ("recuerdos", "media", 100),
                              ("historial", "historica", 200)):
        directory = Path(root) / folder
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.md"
        path.write_text(NOTE.replace("Titulo de prueba", f"Nota {name}"), encoding="utf-8")
        stamp = path.stat().st_mtime - age
        os.utime(path, (stamp, stamp))


def test_ordena_por_fecha_limpia_y_no_modifica():
    with tempfile.TemporaryDirectory() as root:
        make_notes(root)
        before = {p: p.read_bytes() for p in Path(root).rglob("*.md")}
        items = notes.read_recent(root, limit=3)
        assert [i["path"] for i in items] == ["conclusiones/nueva.md", "recuerdos/media.md", "historial/historica.md"], items
        text = items[0]["content"]
        assert text.startswith("# Nota nueva") and "Texto del recuerdo" in text, text
        assert "[[" not in text and "tipo:" not in text and "copia de solo lectura" not in text, text
        assert [i["path"] for i in notes.read_recent(root, limit=9, folder="conclusiones")] == ["conclusiones/nueva.md", "conclusiones/vieja.md"]
        assert {p: p.read_bytes() for p in Path(root).rglob("*.md")} == before  # solo lee


def test_no_lee_fuera_de_la_carpeta_ni_otras_carpetas():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
        make_notes(root)
        (Path(root) / "suelta.md").write_text("fuera de las carpetas de StoneWave", encoding="utf-8")
        secret = Path(outside) / "secreto.md"
        secret.write_text("no debe salir", encoding="utf-8")
        try:
            os.symlink(secret, Path(root) / "conclusiones" / "enlace.md")
        except OSError:
            pass  # sin permiso para enlaces simbolicos (Windows): no hay nada que probar
        paths = [i["path"] for i in notes.read_recent(root, limit=10)]
        assert "suelta.md" not in paths and not any("enlace" in p for p in paths) and len(paths) == 4, paths
        assert notes.read_recent(root, limit=2, folder="../" + Path(outside).name)  # un folder raro se ignora y usa las carpetas normales
        assert all(i["folder"] in ("conclusiones", "recuerdos", "historial") for i in notes.read_recent(root, folder="../x"))


def test_motor_y_herramienta_mcp():
    with tempfile.TemporaryDirectory() as root:
        make_notes(root)
        engine = make_engine()
        with patch.dict(os.environ, {"STONEWAVE_NOTES": root}):
            r = engine.recent_notes(limit=2)
            assert r["status"] == "ok" and len(r["notes"]) == 2, r
            assert engine.recent_notes(limit="x")["status"] == "ok"  # limite invalido: usa 5
            assert len(engine.recent_notes(limit=99)["notes"]) == 4  # tope de 10
            import mcp_server
            with patch.object(mcp_server, "engine", engine):
                out = json.loads(mcp_server.recent_notes(limit=1, folder="recuerdos"))
            assert out["status"] == "ok" and out["notes"][0]["path"] == "recuerdos/media.md", out
        with patch.dict(os.environ, {"STONEWAVE_NOTES": os.path.join(root, "nada")}):
            assert engine.recent_notes()["status"] == "error"
        with patch.dict(os.environ, {"STONEWAVE_NOTES": "off"}):
            assert engine.recent_notes() == {"status": "off", "notes": []}
    with tempfile.TemporaryDirectory() as vacia, patch.dict(os.environ, {"STONEWAVE_NOTES": vacia}):
        assert make_engine().recent_notes()["status"] == "empty"


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
