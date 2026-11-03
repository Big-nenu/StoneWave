"""Adaptador: exportaciones de ChatGPT y de Claude (conversations.json) -> formato canonico .jsonl de StoneWave.

Una conversacion no es un recuerdo, asi que solo se toman TUS mensajes (los que escribiste tu) y se filtran:
  - se omiten los muy cortos ("gracias", "ok"): menos de 40 caracteres (--min-chars)
  - se omiten los muy largos (codigo, logs o textos pegados): mas de 600 caracteres (--max-chars)
  - se omiten las instrucciones personalizadas de ChatGPT y los mensajes repetidos
  - la fecha del recuerdo (created_at) es la del mensaje; source es 'chatgpt' o 'claude'

El formato se detecta solo, conversacion por conversacion. Acepta el conversations.json suelto o el .zip
completo de la exportacion (ChatGPT: Settings > Data controls > Export; Claude: Settings > Privacy > Export data).

Uso:
  python parsers/chat_parser.py conversations.json salida.jsonl [-n NAMESPACE] [--con-titulo] [--min-chars 40] [--max-chars 600]
  python mem_cli.py import export.zip --from chatgpt [--con-titulo]      # convierte e importa de una vez
"""
import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

MIN_CHARS = 40
MAX_CHARS = 600


def _iso(ts):
    """Segundos desde 1970 o texto de fecha -> texto ISO 8601 (None si no hay fecha)."""
    if ts is None or ts == "" or isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)


def _read_conversations(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json"]
            if not names:
                raise ValueError("el .zip no trae conversations.json")
            raw = z.read(names[0]).decode("utf-8-sig")
    else:
        raw = p.read_text(encoding="utf-8-sig")  # utf-8-sig tolera el BOM de Windows
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("conversations", [])
    if not isinstance(data, list):
        raise ValueError("no parece una exportacion de conversaciones (se esperaba una lista)")
    return data


def _chatgpt_messages(conv):
    """[(fecha, texto)] de los mensajes del usuario, en orden cronologico."""
    found = []
    for node in (conv.get("mapping") or {}).values():
        m = node.get("message") if isinstance(node, dict) else None
        if not isinstance(m, dict) or (m.get("author") or {}).get("role") != "user":
            continue
        if (m.get("metadata") or {}).get("is_user_system_message"):  # instrucciones personalizadas, no son del chat
            continue
        parts = (m.get("content") or {}).get("parts") or []
        text = " ".join(p for p in parts if isinstance(p, str))  # imagenes y archivos vienen como dict: se ignoran
        found.append((m.get("create_time") or 0, text))
    found.sort(key=lambda x: x[0])
    return [(t or conv.get("create_time"), text) for t, text in found]


def _claude_messages(conv):
    out = []
    for m in conv.get("chat_messages") or []:
        if m.get("sender") != "human":
            continue
        text = m.get("text")
        if not isinstance(text, str) or not text.strip():  # exportaciones nuevas guardan el texto en bloques
            text = " ".join(b.get("text", "") for b in (m.get("content") or [])
                            if isinstance(b, dict) and b.get("type") == "text")
        out.append((m.get("created_at") or conv.get("created_at"), text))
    return out


def load_records(path, namespace="default", with_title=False, min_chars=MIN_CHARS, max_chars=MAX_CHARS):
    """Lee la exportacion y devuelve (registros_canonicos, estadisticas).

    with_title: antepone el titulo de la conversacion al recuerdo ("Plan de viaje: Quiero ...").
    """
    stats = {"conversations": 0, "messages": 0, "short": 0, "long": 0, "duplicates": 0, "unknown": 0}
    records, seen = [], set()
    for conv in _read_conversations(path):
        if not isinstance(conv, dict):
            stats["unknown"] += 1
            continue
        if "mapping" in conv:
            source, msgs, title = "chatgpt", _chatgpt_messages(conv), conv.get("title")
        elif "chat_messages" in conv:
            source, msgs, title = "claude", _claude_messages(conv), conv.get("name")
        else:
            stats["unknown"] += 1
            continue
        stats["conversations"] += 1
        title = " ".join(title.split()) if isinstance(title, str) else ""
        for ts, text in msgs:
            text = " ".join(str(text).split())
            stats["messages"] += 1
            if len(text) < min_chars:
                stats["short"] += 1
                continue
            if len(text) > max_chars:
                stats["long"] += 1
                continue
            content = f"{title}: {text}" if with_title and title else text
            if content.casefold() in seen:
                stats["duplicates"] += 1
                continue
            seen.add(content.casefold())
            rec = {"content": content, "namespace": namespace, "fijo": False, "energy": 0.5, "source": source}
            if _iso(ts):
                rec["created_at"] = _iso(ts)
            records.append(rec)
    return records, stats


def convert(in_path, out_path, namespace="default", with_title=False, min_chars=MIN_CHARS, max_chars=MAX_CHARS):
    """Convierte la exportacion a out_path (.jsonl canonico). Devuelve las estadisticas (+ 'records')."""
    records, stats = load_records(in_path, namespace, with_title, min_chars, max_chars)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["records"] = len(records)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convierte una exportacion de ChatGPT o Claude al formato .jsonl de StoneWave")
    ap.add_argument("entrada", help="conversations.json o el .zip de la exportacion")
    ap.add_argument("salida", help=".jsonl canonico de StoneWave que se va a crear")
    ap.add_argument("-n", "--namespace", default="default", help="namespace de destino (default: 'default')")
    ap.add_argument("--con-titulo", action="store_true", help="antepone el titulo de la conversacion a cada recuerdo")
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS, help=f"largo minimo de un mensaje (default: {MIN_CHARS})")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS, help=f"largo maximo de un mensaje (default: {MAX_CHARS})")
    args = ap.parse_args(argv)
    try:
        s = convert(args.entrada, args.salida, args.namespace, args.con_titulo, args.min_chars, args.max_chars)
    except FileNotFoundError:
        print(f"No se encontró: {args.entrada}")
        return 1
    except ValueError as ex:
        print(f"No se pudo leer {args.entrada}: {ex}")
        return 1
    print(f"Listo: {s['records']} recuerdos escritos en {args.salida} ({s['conversations']} conversaciones, "
          f"{s['messages']} mensajes tuyos; omitidos: {s['short']} cortos, {s['long']} largos, {s['duplicates']} repetidos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
