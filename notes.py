"""Notas de Obsidian: exporta las conclusiones de StoneWave (y los recuerdos de donde salieron) como notas .md con wikilinks [[...]].

Reglas:
  - StoneWave es la fuente de verdad. Las notas son una COPIA para VER (Graph View). Este modulo solo escribe: nunca lee las notas
    para cambiar la memoria. Lo que edites en una nota se pierde en la siguiente exportacion.
  - Carpetas separadas: <base>/conclusiones/ y <base>/recuerdos/. En el Graph View se ocultan los recuerdos con el filtro
    `-path:recuerdos` y quedan solo las conclusiones.
  - Tercera carpeta: <base>/historial/ guarda las conclusiones que caducaron o se reemplazaron (se ocultan con `-path:historial`).
    Una conclusion que reemplazo a otra la enlaza ("Reemplazo a") y la vieja enlaza a la nueva ("Reemplazada por").
  - Una conclusion que se derivo de otras las enlaza ("Deriva de") y ellas enlazan a la nueva ("De aqui nacieron").
  - Trazabilidad por wikilinks: cada conclusion enlaza a los recuerdos de donde salio ("Viene de") y a otras conclusiones
    ("Relacionada con"); cada recuerdo enlaza de vuelta a las conclusiones que ayudo a formar ("Sirvio para").
  - Solo se tocan archivos que StoneWave genero (llevan `generado_por: "stonewave"` y el `equipo` que los escribio): nunca se pisa ni se
    borra una nota tuya, ni las que escribio otra computadora que comparta la misma carpeta.
  - Solo se exportan los recuerdos que originaron alguna conclusion (los demas no salen de la memoria).
"""
import hashlib
import json
import platform
import re
from datetime import datetime
from pathlib import Path

from llm import env_var

FOLDER_CONCLUSIONS = "conclusiones"
FOLDER_MEMORIES = "recuerdos"
FOLDER_HISTORY = "historial"
_BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_MARK = re.compile(r'^generado_por:\s*"stonewave"\s*$', re.M)
_HOST = re.compile(r'^equipo:\s*(".*")\s*$', re.M)


def notes_dir():
    """Carpeta base de las notas (variable STONEWAVE_NOTES), o None si estan apagadas."""
    value = (env_var("NOTES") or "").strip()
    return None if value.lower() in ("", "off", "false", "0", "no") else value


def this_host():
    return platform.node() or "equipo"


# ----------------------------------------------------------------------
# Nombres y enlaces
# ----------------------------------------------------------------------
def _clean(text, limit):
    t = " ".join(_BAD_CHARS.sub(" ", str(text)).split()).strip(" .")
    if len(t) > limit:
        t = (t[:limit].rsplit(" ", 1)[0] if " " in t[:limit] else t[:limit]).strip(" .,;:-")
    return t or "nota"


def note_name(kind, text):
    """Nombre de archivo (sin .md): el texto limpio + 6 letras de huella. Es estable: el mismo texto da el mismo nombre en
    cualquier computadora (no depende de los IDs de la base de datos, que cambian de una PC a otra)."""
    key = " ".join(str(text).split()).casefold()
    return f"{_clean(text, 70)} ({hashlib.sha1(f'{kind}|{key}'.encode('utf-8')).hexdigest()[:6]})"


def link(name, label):
    """Wikilink con alias: [[nombre|texto]]."""
    label = " ".join(str(label).replace("|", "/").replace("[", "(").replace("]", ")").split())
    if len(label) > 110:
        label = label[:107].rstrip() + "..."
    return f"[[{name}|{label}]]"


def _date(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d") if ts else ""


def _front(items):
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return json.dumps(v, ensure_ascii=False)  # cadenas y listas: JSON en una linea (YAML valido)
    return "---\n" + "\n".join(f"{k}: {val(v)}" for k, v in items) + "\n---\n"


def _state(status):
    return "confirmada" if status == "confirmada" else "hipotesis"


_REASONS = {"caducada": "caducada", "contradicha": "contradicha", "reemplazada": "reemplazada", "excedente": "descartada por el tope",
            "sin_origen": "sin recuerdos de origen"}
_HOW = {"caducada": "caducó: ningún recuerdo nuevo la respaldó y perdió su energía",
        "contradicha": "se descartó porque recuerdos nuevos la contradijeron",
        "reemplazada": "la reemplazó una conclusión nueva que la contradice",
        "excedente": "salió por el tope de hipótesis guardadas",
        "sin_origen": "se descartó porque ya no existen los recuerdos de donde salió"}


def _item_state(item):
    """Estado en texto de una conclusion viva o de una del historial (esas traen 'reason')."""
    if item.get("reason"):
        return _REASONS.get(item["reason"], item["reason"])
    return _state_label(item["status"])


def _state_label(status):
    return "confirmada" if status == "confirmada" else "hipótesis"


# ----------------------------------------------------------------------
# Contenido de las notas
# ----------------------------------------------------------------------
_WARNING = ("> Nota generada por StoneWave: es una copia de solo lectura. La fuente de verdad es la memoria de StoneWave; "
            "si editas esta nota, el siguiente sueño la sobrescribe.")


def render_conclusion(c, memories, relations, names, host, namespace, extra=None):
    """c: conclusion; memories: {id: recuerdo}; relations: relaciones con otras conclusiones; names: {('c'|'m', id): nombre de nota}."""
    extra = extra or {}
    state = _state(c["status"])
    out = _front([
        ("tipo", "conclusion"), ("estado", state), ("energia", round(c["energy"], 2)), ("respaldos", c["support"]),
        ("contradicciones", c["against"]), ("creada", _date(c.get("created"))), ("namespace", namespace),
        ("generado_por", "stonewave"), ("equipo", host), ("tags", ["stonewave", "conclusion", state])])
    out += f"# {' '.join(c['content'].split())}\n\n{_WARNING}\n\n"
    out += (f"**Estado:** {_state_label(c['status'])} (energía {c['energy']:.2f}; "
            f"{c['support']} respaldo(s) nuevo(s), {c['against']} en contra)\n\n")
    out += "## Viene de\n"
    srcs = [f for f in c["sources"] if f in memories]
    out += "\n".join(f"- {link(names[('m', f)], memories[f]['content'])}" for f in srcs) if srcs else "_Sin recuerdos de origen._"
    for title, items in (("Deriva de", extra.get("parents")), ("De aquí nacieron", extra.get("children")),
                         ("Reemplazó a", extra.get("replaced"))):
        if items:
            out += f"\n\n## {title}\n" + "\n".join(f"- {link(names[key], item['content'])} ({_item_state(item)})" for key, item in items)
    out += "\n\n## Relacionada con\n"
    rel = []
    for r in relations:
        other = r["other"]
        how = (f"comparten {r['n']} recuerdo(s)" if r["kind"] == "comparten" else f"parecida (similitud {r['sim']:.2f})")
        rel.append(f"- {link(names[('c', other['id'])], other['content'])} ({_state_label(other['status'])}) · {how}")
    out += "\n".join(rel) if rel else "_Aún sin relación con otras conclusiones._"
    return out + "\n"


def render_memory(m, using, names, host, namespace):
    """m: recuerdo; using: conclusiones que salieron de el."""
    out = _front([
        ("tipo", "recuerdo"), ("fijo", bool(m["pinned"])), ("temas", m["topics"]), ("creado", _date(m.get("created"))),
        ("namespace", namespace), ("generado_por", "stonewave"), ("equipo", host), ("tags", ["stonewave", "recuerdo"])])
    title = " ".join(m["content"].split())
    title = title if len(title) <= 100 else title[:97].rstrip() + "..."
    body = m["content"].strip()
    out += f"# {title}\n\n" + ("" if body == title else f"{body}\n\n") + f"{_WARNING}\n\n## Sirvió para\n"
    out += "\n".join(f"- {link(names[c.get('_key', ('c', c['id']))], c['content'])} ({_item_state(c)})" for c in using)
    return out + "\n"


# ----------------------------------------------------------------------
# Escritura
# ----------------------------------------------------------------------
def render_history(h, memories, names, host, namespace, pick):
    """h: conclusion del historial; pick(llaves) -> [(llave, item)] de las que tienen nota (para los wikilinks)."""
    out = _front([
        ("tipo", "conclusion"), ("estado", h["reason"]), ("era", _state(h["status"])), ("energia", round(h["energy"], 2)),
        ("creada", _date(h.get("created"))), ("termino", _date(h.get("ended"))), ("namespace", namespace),
        ("generado_por", "stonewave"), ("equipo", host), ("tags", ["stonewave", "historial", h["reason"]])])
    out += f"# {' '.join(h['content'].split())}\n\n{_WARNING}\n\n"
    out += (f"**Qué pasó:** {_HOW.get(h['reason'], h['reason'])} el {_date(h.get('ended'))} "
            f"(era {_state_label(h['status'])}, energía {h['energy']:.2f}; {h['support']} respaldo(s), {h['against']} en contra)\n\n")
    out += "## Venía de\n"
    srcs = [f for f in h["sources"] if f in memories]
    out += "\n".join(f"- {link(names[('m', f)], memories[f]['content'])}" for f in srcs) if srcs else "_Sin recuerdos de origen._"
    for title, keys in (("Reemplazada por", [h["replaced_by"]] if h.get("replaced_by") else []), ("Derivaba de", h.get("parent_refs", []))):
        items = pick(keys)
        if items:
            out += f"\n\n## {title}\n" + "\n".join(f"- {link(names[key], item['content'])} ({_item_state(item)})" for key, item in items)
    return out + "\n"


def _is_generated(text):
    return bool(_MARK.search(text[:1500]))


def _owner(text):
    m = _HOST.search(text[:1500])
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def _read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def write_notes(data, base_dir, host=None):
    """Escribe las notas de 'data' (ver StoneWaveEngine.notes_data) dentro de base_dir y limpia las que ya no corresponden.

    Devuelve un dict: written, unchanged, deleted, skipped (notas tuyas con el mismo nombre que no se tocaron), conclusions, memories.
    Solo borra archivos generados por StoneWave desde ESTE equipo."""
    host = host or this_host()
    base = Path(base_dir)
    conc_dir, mem_dir, hist_dir = base / FOLDER_CONCLUSIONS, base / FOLDER_MEMORIES, base / FOLDER_HISTORY
    conc_dir.mkdir(parents=True, exist_ok=True)
    mem_dir.mkdir(parents=True, exist_ok=True)
    if data.get("history"):
        hist_dir.mkdir(parents=True, exist_ok=True)

    concs = {}
    for c in data["conclusions"]:
        concs.setdefault(note_name("conclusion", c["content"]), c)  # dos conclusiones con el mismo texto serian la misma nota
    by_id = {c["id"]: c for c in concs.values()}
    hist = {}
    for h in data.get("history", []):  # el mismo texto puede caducar mas de una vez: la fecha entra en la huella
        hist.setdefault(note_name(f"historial{int(h.get('ended') or 0)}", h["content"]), h)
    memories = data["facts"]
    used = sorted({f for c in list(by_id.values()) + list(hist.values()) for f in c["sources"] if f in memories})
    names = {("c", c["id"]): n for n, c in concs.items()}
    names.update({("h", h["id"]): n for n, h in hist.items()})
    names.update({("m", f): note_name("recuerdo", memories[f]["content"]) for f in used})

    using = {}
    for c in by_id.values():
        for f in c["sources"]:
            if f in memories:
                using.setdefault(f, []).append(c)
    for h in hist.values():
        for f in h["sources"]:
            if f in memories:
                using.setdefault(f, []).append({**h, "_key": ("h", h["id"])})

    info = {("c", c["id"]): c for c in by_id.values()}
    info.update({("h", h["id"]): h for h in hist.values()})

    def pick(keys):
        return [(tuple(k), info[tuple(k)]) for k in keys if tuple(k) in info and tuple(k) in names]

    def extra(c):
        return {"parents": pick(c.get("parent_refs", [])), "children": pick(c.get("child_refs", [])),
                "replaced": pick([("h", i) for i in c.get("replaced", [])])}

    wanted = {}
    for name, c in concs.items():
        rels = [{"other": by_id[r["id"]], **r} for r in data["relations"].get(c["id"], []) if r["id"] in by_id]
        wanted[conc_dir / f"{name}.md"] = render_conclusion(c, memories, rels, names, host, data["namespace"], extra(c))
    for f in used:
        wanted[mem_dir / f"{names[('m', f)]}.md"] = render_memory(memories[f], using[f], names, host, data["namespace"])
    for name, h in hist.items():
        wanted[hist_dir / f"{name}.md"] = render_history(h, memories, names, host, data["namespace"], pick)

    res = {"written": 0, "unchanged": 0, "deleted": 0, "skipped": [], "conclusions": len(concs), "memories": len(used), "history": len(hist)}
    for path, text in wanted.items():
        old = _read(path) if path.exists() else None
        if old is not None and not _is_generated(old):
            res["skipped"].append(path.name)  # una nota tuya con ese nombre: no se toca
        elif old == text:
            res["unchanged"] += 1
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            res["written"] += 1
    for folder in (conc_dir, mem_dir, hist_dir):
        for path in folder.glob("*.md"):
            if path in wanted:
                continue
            old = _read(path)
            if old is not None and _is_generated(old) and _owner(old) == host:  # solo lo que escribio este equipo y ya no corresponde
                path.unlink()
                res["deleted"] += 1
    return res
