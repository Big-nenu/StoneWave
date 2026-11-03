"""Adaptador: notas en Markdown (.md) -> formato canonico .jsonl de StoneWave.

Sirve para traer a StoneWave tus notas (Obsidian, Notion exportado, un .md suelto...). Cada elemento de lista
y cada parrafo se vuelve UN recuerdo. Reglas:
  - viñetas (-, *, +, 1.) y casillas (- [ ] / - [x]): un recuerdo por elemento (las lineas con sangria se unen al elemento)
  - parrafos: lineas seguidas de texto se unen en un recuerdo
  - los titulos (# ...) no son recuerdos; solo dan contexto (ver --con-titulo y --fijo-titulo)
  - se omiten: front matter (--- ... ---), bloques de codigo, tablas, comentarios <!-- -->, reglas horizontales
    y textos de menos de 8 caracteres
  - [texto](url) -> texto; se quitan **negritas** y `codigo`
  - frases repetidas se omiten (se cuentan en las estadisticas)
  - si le das una CARPETA, lee todos los .md de adentro (sin entrar a carpetas ocultas como .obsidian o .git)

Uso:
  python parsers/markdown_parser.py notas.md salida.jsonl [-n NAMESPACE] [--con-titulo] [--fijo-titulo "Sobre mi" ...]
  python mem_cli.py import notas.md --from markdown [--con-titulo]      # convierte e importa de una vez
"""
import argparse
import json
import re
import sys
from pathlib import Path

SOURCE = "markdown"
MIN_CHARS = 8

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_CHECKBOX = re.compile(r"^\[[ xX]\]\s+")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_FENCE = re.compile(r"^\s*(```|~~~)")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def _clean(text):
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = text.replace("`", "")
    return " ".join(text.split())


def _blocks(text):
    """Devuelve [(titulo_actual, texto)] de un documento Markdown."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":  # front matter (YAML)
        for j in range(1, len(lines)):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break
    out, heading = [], ""
    para, item = [], None
    in_code = in_comment = False

    def flush():
        nonlocal para, item
        for parts in (item, para):
            if parts:
                out.append((heading, _clean(" ".join(parts))))
        para, item = [], None

    for raw in lines[i:]:
        line = raw.rstrip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if _FENCE.match(line):
            flush()
            in_code = not in_code
            continue
        if in_code:
            continue
        stripped = line.strip()
        if stripped.startswith("<!--"):
            flush()
            in_comment = "-->" not in stripped
            continue
        if not stripped:
            flush()
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            heading = _clean(m.group(2))
            continue
        if _RULE.match(line) or stripped.startswith("|"):  # regla horizontal o tabla
            flush()
            continue
        m = _BULLET.match(line)
        if m:
            flush()
            item = [_CHECKBOX.sub("", m.group(1))]
            continue
        if item is not None and raw[:1] in (" ", "\t"):  # continuacion con sangria de un elemento de lista
            item.append(stripped)
            continue
        if item is not None:
            flush()
        para.append(stripped.lstrip("> "))
    flush()
    return out


def _files(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*.md")
                  if not any(part.startswith(".") for part in f.relative_to(p).parts[:-1]))


def load_records(path, namespace="default", pinned_headings=(), with_heading=False, min_chars=MIN_CHARS):
    """Lee un .md (o una carpeta de .md) y devuelve (registros_canonicos, estadisticas).

    pinned_headings: titulos (sin importar mayusculas) cuyos recuerdos van como 'fijo' (ej. "Sobre mi").
    with_heading: antepone el titulo al recuerdo ("Mi perro: Se llama Bruno") para que no pierda contexto.
    """
    pinned = {str(h).strip().casefold() for h in pinned_headings}
    stats = {"files": 0, "blocks": 0, "short": 0, "duplicates": 0}
    records, seen = [], set()
    for f in _files(path):
        stats["files"] += 1
        with open(f, encoding="utf-8-sig") as fh:  # utf-8-sig tolera el BOM de Windows
            blocks = _blocks(fh.read())
        for heading, text in blocks:
            stats["blocks"] += 1
            if len(text) < min_chars:
                stats["short"] += 1
                continue
            content = text
            if with_heading and heading and not text.casefold().startswith(heading.casefold()):
                content = f"{heading}: {text}"
            if content.casefold() in seen:
                stats["duplicates"] += 1
                continue
            seen.add(content.casefold())
            records.append({"content": content, "namespace": namespace, "fijo": heading.casefold() in pinned,
                            "energy": 0.5, "source": SOURCE})
    return records, stats


def convert(in_path, out_path, namespace="default", pinned_headings=(), with_heading=False):
    """Convierte in_path (.md o carpeta) a out_path (.jsonl canonico). Devuelve las estadisticas (+ 'records')."""
    records, stats = load_records(in_path, namespace, pinned_headings, with_heading)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["records"] = len(records)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convierte notas en Markdown al formato .jsonl de StoneWave")
    ap.add_argument("entrada", help="archivo .md o carpeta con .md")
    ap.add_argument("salida", help=".jsonl canonico de StoneWave que se va a crear")
    ap.add_argument("-n", "--namespace", default="default", help="namespace de destino (default: 'default')")
    ap.add_argument("--con-titulo", action="store_true", help="antepone el titulo de cada seccion a sus recuerdos")
    ap.add_argument("--fijo-titulo", action="append", default=[], metavar="TITULO",
                    help="titulo cuyos recuerdos van como 'fijo' (se puede repetir; ej. --fijo-titulo \"Sobre mi\")")
    args = ap.parse_args(argv)
    try:
        s = convert(args.entrada, args.salida, args.namespace, args.fijo_titulo, args.con_titulo)
    except FileNotFoundError:
        print(f"No se encontró: {args.entrada}")
        return 1
    print(f"Listo: {s['records']} recuerdos escritos en {args.salida} ({s['files']} archivo(s); "
          f"omitidos: {s['short']} muy cortos, {s['duplicates']} repetidos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
