"""Registro real del sueño manual y automático con datos sintéticos y modelos simulados."""
import gc
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_engine import make_engine


def test_dream_activity():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "prueba.db"
        notes = root / "notas"
        notes.mkdir()
        with patch.dict(os.environ, {"STONEWAVE_DB": str(database), "STONEWAVE_NOTES": str(notes),
                                     "STONEWAVE_NAMESPACE": "default"}):
            import mcp_server

            engine = make_engine(str(database))
            engine._ask_llm_conclusion = lambda facts: "Los ejercicios prácticos ayudan a aprender."
            engine._ask_llm_support = lambda fact, conclusion: "RESPALDA"
            with patch.object(mcp_server, "engine", engine):
                assert json.loads(mcp_server.dream_activity()) == {"status": "never"}
                a = "Aprender programación con ejercicios prácticos semanales"
                b = "Aprender matemáticas con ejercicios prácticos semanales"
                engine.inject(a)
                engine.inject(b)
                assert "Sleeptime" in mcp_server.force_dream()
                manual = json.loads(mcp_server.dream_activity())
                assert manual["source"] == "manual" and manual["links_count"] >= 1
                assert {fact["content"] for fact in manual["links"][0]["facts"]} == {a, b}
                assert manual["conclusions"][0]["content"] == "Los ejercicios prácticos ayudan a aprender."
                assert manual["notes_status"] == "ok" and manual["notes_created"] and not manual["notes_updated"]
                filename = manual["notes_created"][0]
                note = notes / filename
                note.write_text(note.read_text(encoding="utf-8") + "\ncontenido anterior", encoding="utf-8")

                with (patch.object(engine, "dream_due", return_value=True),
                      patch.object(mcp_server, "_last_activity", 0),
                      patch.object(mcp_server.time, "sleep", side_effect=[None, StopIteration])):
                    try:
                        mcp_server._dream_loop()
                    except StopIteration:
                        pass
                automatic = json.loads(mcp_server.dream_activity())
                assert automatic["source"] == "automático"
                assert filename in automatic["notes_updated"]
                assert not automatic["notes_created"]

                engine.forget(1)
                assert any(fact["id"] == 1 and fact["content"] is None
                           for link in engine.dream_activity()["links"] for fact in link["facts"])
                with closing(sqlite3.connect(database)) as conn, conn:
                    conn.execute("UPDATE meta SET value='1' WHERE key='last_dream:default'")
                assert json.loads(mcp_server.dream_activity())["status"] == "date_only"
                assert engine.dream_activity("otro") == {"status": "never"}
            engine.close()
            mcp_server.engine.close()
            gc.collect()


if __name__ == "__main__":
    test_dream_activity()
    print("PASS: actividad manual y automática, enlaces, conclusiones y notas.")
