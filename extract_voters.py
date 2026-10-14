#!/usr/bin/env python3
"""Extract voter records from a Rajasthan PRI electoral-roll PDF.

The roll's Hindi is stored in a legacy font whose Unicode map is wrong.
Glyphs are decoded from that font, then written to a CSV. No language model
is used.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import freetype
import pymupdf
from fontTools.ttLib import TTFont
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate

ROOT = Path(__file__).resolve().parent
PDF_PATH = ROOT / "data" / "pdf" / "SALWAKALAN-Ward No-001.pdf"
OUTPUT_PATH = ROOT / "data" / "output" / "voters.csv"

COLUMNS = [
    "ward",
    "booth_number",
    "serial_number",
    "voter_id_number",
    "name_hi",
    "relative_type_hi",
    "relative_name_hi",
    "house_number_hi",
    "gender_hi",
    "name_en",
    "relative_type_en",
    "relative_name_en",
    "house_number_en",
    "gender_en",
    "address_hi",
    "address_en",
    "age",
]

# Reference-font character code -> (unicode, kind)
# kind: base, post (matra after the consonant), pre (ि before the next
# consonant), pre_anus (ि plus anusvara on that consonant), reph, anus
GLYPHS: dict[int, tuple[str, str]] = {
    0x20: ("र", "base"),
    0x21: ("ा", "post"),
    0x22: ("ज्", "base"),
    0x23: ("य", "base"),
    0x24: ("ि", "pre"),
    0x25: ("न", "base"),
    0x26: ("व", "base"),
    0x27: ("र्", "reph"),
    0x28: ("च", "base"),
    0x29: ("आ", "base"),
    0x2A: ("ो", "post"),
    0x2B: ("ग", "base"),
    0x2C: ("ज", "base"),
    0x2D: ("स्", "base"),
    0x2E: ("थ", "base"),
    0x2F: ("प", "base"),
    0x30: ("ं", "anus"),
    0x31: ("त", "base"),
    0x32: ("ु", "post"),
    0x33: ("क", "base"),
    0x34: ("म", "base"),
    0x35: ("ल", "base"),
    0x36: ("ी", "post"),
    0x37: ("ि", "pre"),
    0x38: ("ष", "base"),
    0x39: ("द", "base"),
    0x3A: ("ध", "base"),
    0x3B: ("॰", "base"),
    0x3C: ("स", "base"),
    0x3D: ("क्ष", "base"),
    0x3E: ("े", "post"),
    0x3F: ("त्र", "base"),
    0x40: ("ण्", "base"),
    0x41: ("ड", "base"),
    0x42: ("क्र", "base"),
    0x43: ("ग्र", "base"),
    0x44: ("भ", "base"),
    0x45: ("ी", "post"),
    0x46: ("ख्", "base"),
    0x47: ("ए", "base"),
    0x48: ("ढ़", "base"),
    0x49: ("ह", "base"),
    0x4A: ("ण", "base"),
    0x4B: ("अ", "base"),
    0x4C: ("द्य", "base"),
    0x4D: ("ि", "pre"),
    0x4E: ("प्र", "base"),
    0x4F: ("श", "base"),
    0x50: ("ब", "base"),
    0x51: ("ू", "post"),
    0x52: ("ों", "post"),
    0x53: ("म्", "base"),
    0x54: ("रू", "base"),
    0x55: ("स्त्र", "base"),
    0x56: ("उ", "base"),
    0x57: ("च्च", "base"),
    0x58: ("ई", "base"),
    0x59: ("ृ", "post"),
    0x5A: ("ि", "pre_anus"),
    0x5B: ("ें", "post"),
    0x5C: ("क्त", "base"),
    0x5D: ("छ", "base"),
    0x5E: ("श्र", "base"),
    0x5F: ("रु", "base"),
    0x60: ("ख", "base"),
    0x61: ("ौ", "post"),
    0x62: ("ड़", "base"),
    0x63: ("े", "post"),
    0x64: ("द्र", "base"),
    0x65: ("ल्", "base"),
    0x66: ("घ्", "base"),
    0x67: ("न्", "base"),
    0x68: ("घ", "base"),
    0x69: ("फ", "base"),
    0x6A: ("ठ", "base"),
    0x6B: ("ओ", "base"),
    0x6C: ("न्न", "base"),
    0x6D: ("द्ध", "base"),
    0x6E: ("छ", "base"),
    0x6F: ("ट", "base"),
    0x70: ("ऊ", "base"),
    0x71: ("ड़", "base"),
    0x72: ("ं", "anus"),
    0x73: ("श्व", "base"),
    0x74: ("श्", "base"),
    0x75: ("इ", "base"),
    0x76: ("ढ", "base"),
    0x77: ("ट्", "base"),
    0x78: ("झ", "base"),
}

EPIC_RE = re.compile(r"^(?:[A-Z]{3}\d{6,8}|RJ/\d+/\d+/\d+)$")
HEADER_HINTS = (
    "जिला",
    "पंचायत",
    "ग्रामपंचायत",
    "वार्ड",
    "विधानसभा",
    "राज्य",
    "निर्वाचन",
    "पृष्ठ",
    "जनवरी",
    "सदस्य",
    "क्रमांक",
    "पुनरीक्षण",
    "पूरक",
    "घटक",
    "विलोपन",
    "परिवर्धन",
    "संशोधन",
    "नामावली",
    "मतदाता",
    "हस्ताक्षर",
    "अनुसार",
    "संख्या",
)

RELATIVE_TYPES = (
    ("पिता", "Father"),
    ("पति", "Husband"),
    ("माता", "Mother"),
    ("अन्य", "Other"),
)


@dataclass
class GlyphInfo:
    text: str
    kind: str
    advance: float
    xmin: float
    xmax: float


@dataclass
class TextAtom:
    x: float
    y: float
    text: str
    source: str  # hindi, latin, serial


def ward_from_filename(path: Path) -> str:
    match = re.search(r"ward\s*no\.?\s*[-_\s]*(\d+)", path.name, re.I)
    if not match:
        raise ValueError(f"Could not read a ward number from {path.name}")
    return str(int(match.group(1)))


def _font_code_map(font_bytes: bytes) -> dict[int, int]:
    """Map glyph index -> reference character code (from the glyph name)."""
    path = Path("/tmp/pdf_fonts/_current.ttf")
    path.write_bytes(font_bytes)
    font = TTFont(str(path))
    mapping: dict[int, int] = {}
    for index, name in enumerate(font.getGlyphOrder()):
        if not name.startswith("uni"):
            continue
        try:
            codepoint = int(name[3:], 16)
        except ValueError:
            continue
        if 0xF020 <= codepoint <= 0xF0FF:
            mapping[index] = codepoint & 0xFF
    return mapping


def _glyph_image(font_path: str, gid: int):
    face = freetype.Face(font_path)
    face.set_char_size(64 * 64)
    face.load_glyph(gid, freetype.FT_LOAD_RENDER)
    bitmap = face.glyph.bitmap
    if not bitmap.width or not bitmap.rows:
        return None
    import numpy as np
    from PIL import Image

    pixels = np.array(bitmap.buffer, dtype=np.uint8).reshape(
        bitmap.rows, bitmap.pitch
    )[:, : bitmap.width]
    ys, xs = np.where(pixels > 40)
    if len(xs) == 0:
        return None
    crop = pixels[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    image = Image.fromarray(crop)
    image.thumbnail((48, 48))
    canvas = Image.new("L", (48, 48), 0)
    canvas.paste(image, ((48 - image.width) // 2, (48 - image.height) // 2))
    return np.array(canvas).astype("float32") / 255.0


def _similarity(left, right) -> float:
    import numpy as np

    a = left.ravel()
    b = right.ravel()
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


class FontBook:
    """Decode every embedded Hindi subset back to the reference glyph map."""

    def __init__(self, document: pymupdf.Document):
        self._by_xref: dict[int, dict[int, GlyphInfo]] = {}
        self._images: dict[int, object] | None = None
        self._ref_path = ""
        self._prepare_reference(document)

    def _prepare_reference(self, document: pymupdf.Document) -> None:
        info = document.extract_font(95)
        ref_path = "/tmp/pdf_fonts/f95.ttf"
        Path(ref_path).write_bytes(info[3])
        self._ref_path = ref_path
        self._ref_gid = _font_code_map(info[3])
        # glyph index -> code, invert
        self._ref_code_gid = {code: gid for gid, code in self._ref_gid.items()}
        self._images = {}
        for code, gid in self._ref_code_gid.items():
            image = _glyph_image(ref_path, gid)
            if image is not None:
                self._images[code] = image
        self._by_xref[95] = self._metrics_for(info[3], {code: code for code in GLYPHS})

    def _metrics_for(self, font_bytes: bytes, code_to_ref: dict[int, int]) -> dict[int, GlyphInfo]:
        path = "/tmp/pdf_fonts/_metrics.ttf"
        Path(path).write_bytes(font_bytes)
        tt = TTFont(path)
        order = tt.getGlyphOrder()
        glyf = tt["glyf"]
        face = freetype.Face(path)
        face.set_char_size(1000 * 64)
        gid_to_code = _font_code_map(font_bytes)
        infos: dict[int, GlyphInfo] = {}
        for gid, code in gid_to_code.items():
            ref = code_to_ref.get(code)
            if ref is None or ref not in GLYPHS:
                continue
            text, kind = GLYPHS[ref]
            face.load_glyph(gid, freetype.FT_LOAD_NO_SCALE)
            advance = face.glyph.advance.x / 64.0
            glyph = glyf[order[gid]]
            xmin = float(getattr(glyph, "xMin", 0))
            xmax = float(getattr(glyph, "xMax", advance))
            infos[gid] = GlyphInfo(text, kind, advance, xmin, xmax)
        return infos

    def for_xref(self, document: pymupdf.Document, xref: int) -> dict[int, GlyphInfo]:
        if xref in self._by_xref:
            return self._by_xref[xref]
        info = document.extract_font(xref)
        gid_to_code = _font_code_map(info[3])
        if xref == 95:
            code_to_ref = {code: code for code in gid_to_code.values()}
        else:
            path = f"/tmp/pdf_fonts/f{xref}.ttf"
            Path(path).write_bytes(info[3])
            code_to_ref = {}
            for gid, code in gid_to_code.items():
                image = _glyph_image(path, gid)
                if image is None or not self._images:
                    continue
                best_code, best_score = None, -1.0
                for ref_code, ref_image in self._images.items():
                    score = _similarity(image, ref_image)
                    if score > best_score:
                        best_code, best_score = ref_code, score
                if best_code is not None:
                    code_to_ref[code] = best_code
        self._by_xref[xref] = self._metrics_for(info[3], code_to_ref)
        return self._by_xref[xref]


def hindi_font_xref(page: pymupdf.Page) -> int | None:
    chosen = None
    chosen_size = -1
    for font in page.get_fonts():
        xref = font[0]
        name = font[3] or ""
        if "ArialUnicode" not in name:
            continue
        # The Hindi subset is much larger than the Latin subset.
        extracted = page.parent.extract_font(xref)
        length = len(extracted[3] or b"")
        if length > chosen_size:
            chosen_size = length
            chosen = xref
    return chosen


def decode_glyphs(glyphs: list[tuple[float, float, GlyphInfo]], font_size: float) -> str:
    """Turn positioned glyphs into Unicode, in logical order."""
    if not glyphs:
        return ""

    placed = []
    for origin_x, _origin_y, info in glyphs:
        # Marks are ordered before a consonant that shares their origin,
        # so a reph is applied to the previous syllable.
        mark_first = 0 if info.kind in {"post", "anus", "reph"} else 1
        placed.append((origin_x, mark_first, info))
    placed.sort(key=lambda item: (item[0], item[1]))

    words: list[list[tuple[float, GlyphInfo]]] = []
    current: list[tuple[float, GlyphInfo]] = []
    last_origin = None
    for origin_x, _mark_first, info in placed:
        previous_kind = current[-1][1].kind if current else ""
        if (
            current
            and last_origin is not None
            and info.kind == "base"
            and previous_kind not in {"pre", "pre_anus"}
            and origin_x - last_origin > 14
        ):
            words.append(current)
            current = []
        current.append((origin_x, info))
        if info.kind == "base":
            last_origin = origin_x
    if current:
        words.append(current)

    scale = font_size / 1000.0
    return " ".join(
        text for word in words if (text := _decode_word(word, scale))
    ).strip()


def _finish_cluster(cluster: list[str]) -> list[str]:
    """Keep anusvara after vowel signs: मां rather than मंा."""
    marks = [token for token in cluster if token == "ं"]
    body = [token for token in cluster if token != "ं"]
    return body + marks


def _decode_word(glyphs: list[tuple[float, GlyphInfo]], scale: float) -> str:
    clusters: list[list[str]] = []
    cluster_x: list[float] = []
    pending: list[str] = []
    pending_anus = False

    def flush_pending(cluster: list[str]) -> None:
        nonlocal pending_anus
        for token in pending:
            cluster.append(token)
        pending.clear()
        if pending_anus:
            cluster.append("ं")
            pending_anus = False

    for origin, info in glyphs:
        if info.kind == "base":
            if clusters:
                clusters[-1] = _finish_cluster(clusters[-1])
            cluster = [info.text]
            flush_pending(cluster)
            clusters.append(cluster)
            cluster_x.append(origin)
        elif info.kind == "pre":
            pending.append(info.text)
        elif info.kind == "pre_anus":
            pending.append("ि")
            pending_anus = True
        elif info.kind == "reph":
            if clusters:
                clusters[-1].insert(0, "र्")
            else:
                pending.insert(0, "र्")
        elif clusters and info.advance <= 0:
            ink = origin + ((info.xmin + info.xmax) / 2.0) * scale
            target = min(range(len(clusters)), key=lambda index: abs(cluster_x[index] - ink))
            clusters[target].append(info.text)
        elif clusters:
            clusters[-1].append(info.text)
        elif pending:
            pending.append(info.text)

    if pending and clusters:
        clusters[-1].extend(pending)
        if pending_anus:
            clusters[-1].append("ं")
    elif pending:
        clusters.append(list(pending))
    if clusters:
        clusters[-1] = _finish_cluster(clusters[-1])

    return "".join("".join(cluster) for cluster in clusters)


def pretty_english(hindi: str) -> str:
    if not hindi:
        return ""
    pieces = []
    buffer = []

    def flush() -> None:
        if not buffer:
            return
        raw = transliterate("".join(buffer), sanscript.DEVANAGARI, sanscript.ITRANS)
        pieces.append(_clean_itrans(raw))
        buffer.clear()

    for char in hindi:
        if "DEVANAGARI" in unicodedata.name(char, ""):
            buffer.append(char)
        else:
            flush()
            pieces.append(char)
    flush()
    text = "".join(pieces)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def _clean_itrans(raw: str) -> str:
    text = raw
    # Nukta consonants before the long-vowel letters are folded.
    text = (
        text.replace(".Dh", "dh")
        .replace(".Th", "th")
        .replace(".D", "d")
        .replace(".N", "n")
        .replace(".n", "n")
        .replace("~N", "n")
        .replace("~n", "n")
    )
    # Drop the inherent short vowel at the end of a word, keeping ा (A).
    text = re.sub(r"a(?=$|[^a-zA-Z])", "", text)
    replacements = [
        ("RRi", "ri"),
        ("R^i", "ri"),
        ("LLi", "li"),
        ("aa", "a"),
        ("ii", "i"),
        ("uu", "u"),
        ("Ai", "ai"),
        ("Au", "au"),
        ("A", "a"),
        ("I", "i"),
        ("U", "u"),
        ("E", "e"),
        ("O", "o"),
        ("M", "n"),
        ("^", ""),
        ("w", "v"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("Sha", "sha").replace("Sh", "sh")
    # ओमप्रकाश is written Omprakash, not Omaprakash.
    text = re.sub(r"(?i)(?<![a-z])oma(?=[bcdfghjklmnpqrstvwyz])", "om", text)
    words = []
    for word in re.split(r"(\s+)", text):
        if word.isalpha():
            words.append(word[:1].upper() + word[1:].lower())
        else:
            words.append(word)
    return "".join(words)


def normalize_gender(text: str) -> tuple[str, str]:
    compact = text.replace(" ", "")
    if "महिला" in compact or "स्त्री" in compact or "पुरूष" not in compact and "स्त्र" in compact:
        if "पुरुष" in compact or "पुरूष" in compact:
            pass
        else:
            return "महिला", "Female"
    if "पुरुष" in compact or "पुरूष" in compact:
        return "पुरुष", "Male"
    if "स्त्री" in compact or "महिला" in compact:
        return "महिला", "Female"
    return "", ""


def relative_from_line(line: str) -> tuple[str, str, str]:
    """Return relative type hindi, english, and the relative's name."""
    cleaned = line.replace(":", " ").replace("  ", " ")
    for hindi, english in RELATIVE_TYPES:
        marker = f"{hindi} का नाम"
        if marker in cleaned or cleaned.startswith(hindi):
            name = cleaned
            name = name.replace(marker, " ")
            if name.strip() == cleaned.strip():
                name = re.sub(rf"^{hindi}", " ", name)
            name = re.sub(r"\s+", " ", name).strip(" :-")
            return hindi, english, name
    return "", "", re.sub(r"\s+", " ", cleaned).strip(" :-")


def name_from_line(line: str) -> str:
    text = line.replace(":", " ")
    text = re.sub(r"^नाम\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" :-")


def is_header(text: str) -> bool:
    return any(hint in text for hint in HEADER_HINTS)


def is_locality(text: str) -> bool:
    if not text or is_header(text):
        return False
    if any(label in text for label in ("नाम", "लिंग", "आयु", "मकान")):
        return False
    hints = ("बास", "नगर", "ढाणी", "कला", "पुरा", "विहार", "कॉलोनी", "धानी", "बाड़")
    return any(hint in text for hint in hints) or ("," in text and len(text) > 6)


def _dedupe_key(x: float, y: float, text: str) -> tuple:
    return (round(x, 1), round(y, 1), text)


def page_atoms(page: pymupdf.Page, book: FontBook, xref: int | None) -> list[TextAtom]:
    glyph_infos = book.for_xref(page.parent, xref) if xref else {}
    atoms: list[TextAtom] = []
    seen: set[tuple] = set()
    for item in page.get_texttrace():
        chars = item["chars"]
        if not chars:
            continue
        font_name = item["font"]
        size = item["size"]
        # Group this run.
        if font_name.startswith("Times"):
            text = "".join(chr(char[0]) for char in chars).strip()
            if not text:
                continue
            origin = chars[0][2]
            key = _dedupe_key(origin[0], origin[1], text)
            if key in seen:
                continue
            seen.add(key)
            atoms.append(TextAtom(origin[0], origin[1], text, "serial"))
            continue

        hindi_chars = []
        latin = []
        for ucs, gid, origin, _bbox in chars:
            char = chr(ucs)
            info = glyph_infos.get(gid)
            looks_hindi = info is not None and not (32 <= ucs < 127)
            if looks_hindi and "ArialUnicode" in font_name:
                hindi_chars.append((origin[0], origin[1], info, size))
            else:
                latin.append((origin[0], origin[1], char))

        if hindi_chars:
            # Group into a line later; emit one atom per visual line fragment.
            hindi_chars.sort(key=lambda entry: (entry[1], entry[0]))
            groups: list[list] = []
            current = []
            for entry in hindi_chars:
                if current and abs(entry[1] - current[-1][1]) > 2:
                    groups.append(current)
                    current = []
                current.append(entry)
            if current:
                groups.append(current)
            for group in groups:
                decoded = decode_glyphs(
                    [(entry[0], entry[1], entry[2]) for entry in group],
                    group[0][3],
                )
                if not decoded:
                    continue
                key = _dedupe_key(group[0][0], group[0][1], decoded)
                if key in seen:
                    continue
                seen.add(key)
                atoms.append(TextAtom(group[0][0], group[0][1], decoded, "hindi"))

        if latin:
            text = "".join(char for _x, _y, char in latin)
            text = text.strip()
            if not text or text in {"Photo is", "Available", "Photo is Available"}:
                continue
            origin_x, origin_y = latin[0][0], latin[0][1]
            key = _dedupe_key(origin_x, origin_y, text)
            if key in seen:
                continue
            seen.add(key)
            atoms.append(TextAtom(origin_x, origin_y, text, "latin"))
    return atoms


def _column_box(epic_x: float, epic_y: float) -> tuple[float, float, float, float]:
    if epic_x < 190:
        left = 28
    elif epic_x < 370:
        left = 200
    else:
        left = 370
    return left, epic_y - 12, left + 180, epic_y + 72


def _inside(atom: TextAtom, box: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = box
    return left <= atom.x <= right and top <= atom.y <= bottom


def _serial_value(text: str) -> str:
    match = re.search(r"(\d+)", text)
    return match.group(1) if match else ""


def _hindi_lines(atoms: list[TextAtom]) -> list[str]:
    hindi = [atom for atom in atoms if atom.source == "hindi"]
    hindi.sort(key=lambda atom: (atom.y, atom.x))
    groups: list[list[TextAtom]] = []
    for atom in hindi:
        if not groups or atom.y - groups[-1][0].y > 1.2:
            groups.append([atom])
        else:
            groups[-1].append(atom)
    lines = []
    for group in groups:
        group.sort(key=lambda atom: atom.x)
        lines.append(" ".join(atom.text for atom in group))
    return lines


def _number_value(text: str) -> str:
    match = re.search(r"[0-9A-Za-z][0-9A-Za-z/\-]*", text.replace(" ", ""))
    return match.group(0) if match else text.strip()


def parse_cards(atoms: list[TextAtom]) -> tuple[list[dict], list[tuple[float, str]]]:
    epics = [atom for atom in atoms if atom.source == "latin" and EPIC_RE.match(atom.text.replace(" ", ""))]
    # Dedupe epics that were painted twice.
    unique = []
    seen = set()
    for atom in epics:
        key = (round(atom.x, 0), round(atom.y, 0), atom.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(atom)

    cards = []
    used_boxes = []
    for epic in unique:
        box = _column_box(epic.x, epic.y)
        used_boxes.append(box)
        inside = [atom for atom in atoms if _inside(atom, box)]
        serials = [
            atom
            for atom in inside
            if atom.source == "serial" and abs(atom.y - epic.y) < 8
        ]
        serial_text = ""
        if serials:
            serials.sort(key=lambda atom: (abs(atom.y - epic.y), atom.x))
            serial_text = serials[0].text
        numbers = []
        for atom in inside:
            if atom.source != "latin":
                continue
            if EPIC_RE.match(atom.text.replace(" ", "")):
                continue
            if re.search(r"\d", atom.text):
                numbers.append(atom)
        house = ""
        age = ""
        if numbers:
            numbers.sort(key=lambda atom: atom.y)
            # House sits above age. A single number is the age when it is low,
            # but cards always print both. The lower number is the age.
            if len(numbers) == 1:
                only = _number_value(numbers[0].text)
                if numbers[0].y > epic.y + 48:
                    age = only
                else:
                    house = only
            else:
                house = _number_value(numbers[-2].text) if len(numbers) >= 2 else ""
                # If three numbers appear, house is the middle-upper one and age the lowest.
                age_atom = max(numbers, key=lambda atom: atom.y)
                house_atom = min(
                    (atom for atom in numbers if atom is not age_atom),
                    key=lambda atom: abs(atom.y - (epic.y + 42)),
                )
                house = _number_value(house_atom.text)
                age = _number_value(age_atom.text)

        lines = _hindi_lines(inside)

        name = ""
        relative_type = ""
        relative_type_en = ""
        relative_name = ""
        gender_hi = ""
        gender_en = ""
        for line in lines:
            gender_hit = normalize_gender(line)
            if gender_hit[0]:
                gender_hi, gender_en = gender_hit
            if "का नाम" in line or line.startswith("पिता") or line.startswith("पति") or line.startswith("माता"):
                relative_type, relative_type_en, relative_name = relative_from_line(line)
            elif "नाम" in line and not name:
                name = name_from_line(line)

        age_digits = re.sub(r"\D", "", age)
        cards.append(
            {
                "y": epic.y,
                "x": epic.x,
                "serial_number": _serial_value(serial_text),
                "voter_id_number": epic.text.replace(" ", ""),
                "name_hi": name,
                "relative_type_hi": relative_type,
                "relative_type_en": relative_type_en,
                "relative_name_hi": relative_name,
                "house_number_hi": house,
                "gender_hi": gender_hi,
                "gender_en": gender_en,
                "age": age_digits,
            }
        )

    seen_serials = set()
    for serial in atoms:
        if serial.source != "serial":
            continue
        key = (round(serial.x), round(serial.y), serial.text)
        if key in seen_serials:
            continue
        seen_serials.add(key)
        number = _serial_value(serial.text)
        if not number:
            continue
        if any(abs(serial.y - card["y"]) < 10 and abs(serial.x - card["x"]) < 90 for card in cards):
            continue
        box = _column_box(serial.x + 30, serial.y + 2)
        inside = [atom for atom in atoms if _inside(atom, box)]
        if not any(atom.source == "hindi" and atom.text == "नाम" and serial.y < atom.y < serial.y + 40 for atom in inside):
            continue
        numbers = [
            atom for atom in inside
            if atom.source == "latin" and re.search(r"\d", atom.text) and not EPIC_RE.match(atom.text.replace(" ", ""))
        ]
        house = ""
        age = ""
        anchor_y = serial.y + 2
        if len(numbers) >= 2:
            age_atom = max(numbers, key=lambda atom: atom.y)
            house_atom = min(
                (atom for atom in numbers if atom is not age_atom),
                key=lambda atom: abs(atom.y - (anchor_y + 42)),
            )
            house = _number_value(house_atom.text)
            age = _number_value(age_atom.text)
        name = ""
        relative_type = relative_type_en = relative_name = ""
        gender_hi = gender_en = ""
        for line in _hindi_lines(inside):
            gender_hit = normalize_gender(line)
            if gender_hit[0]:
                gender_hi, gender_en = gender_hit
            if "का नाम" in line or line.startswith(("पिता", "पति", "माता")):
                relative_type, relative_type_en, relative_name = relative_from_line(line)
            elif "नाम" in line and not name:
                name = name_from_line(line)
        if not name:
            continue
        used_boxes.append(box)
        cards.append({
            "y": serial.y,
            "x": serial.x,
            "serial_number": number,
            "voter_id_number": "",
            "name_hi": name,
            "relative_type_hi": relative_type,
            "relative_type_en": relative_type_en,
            "relative_name_hi": relative_name,
            "house_number_hi": house,
            "gender_hi": gender_hi,
            "gender_en": gender_en,
            "age": re.sub(r"\D", "", age),
        })

    outside = [
        atom
        for atom in atoms
        if atom.source in {"hindi", "latin"}
        and not any(_inside(atom, box) for box in used_boxes)
    ]
    lines: dict[int, list[TextAtom]] = {}
    for atom in outside:
        lines.setdefault(round(atom.y), []).append(atom)
    localities = []
    for y, parts in lines.items():
        parts.sort(key=lambda atom: atom.x)
        text = " ".join(part.text for part in parts)
        text = re.sub(r"\s*:\s*", ": ", text)
        text = re.sub(r"\s*,\s*", ", ", text)
        text = re.sub(r"\s+", " ", text).strip(" ,")
        if is_locality(text):
            localities.append((float(y), text))
    localities.sort()
    cards.sort(key=lambda card: (round(card["y"] / 20.0), card["x"]))
    return cards, localities


def apply_locality(cards: list[dict], localities: list[tuple[float, str]], carry: str) -> str:
    for card in cards:
        active = carry
        for y, text in localities:
            if y < card["y"] - 2:
                active = text
        card["locality"] = active
        carry = active or carry
    if localities:
        # A locality printed below the last card still updates the carry
        # for the next page when it sits under the final row.
        last_y = max(y for y, _text in localities)
        if cards and last_y > cards[-1]["y"]:
            carry = localities[-1][1]
        elif not cards:
            carry = localities[-1][1]
    return carry


def build_row(card: dict, ward: str) -> dict:
    locality = card.get("locality") or ""
    house = card["house_number_hi"]
    locality_en = pretty_english(locality)
    house_en = pretty_english(house) if re.search(r"[^\d/\-]", house) else house
    address_hi = f"मकान संख्या: {house}, {locality}".strip().rstrip(",").strip()
    address_en = f"House No: {house_en}, {locality_en}".strip().rstrip(",").strip()
    name_en = pretty_english(card["name_hi"])
    relative_en = pretty_english(card["relative_name_hi"])
    return {
        "ward": ward,
        "booth_number": "",
        "serial_number": card["serial_number"],
        "voter_id_number": card["voter_id_number"],
        "name_hi": card["name_hi"],
        "relative_type_hi": card["relative_type_hi"],
        "relative_name_hi": card["relative_name_hi"],
        "house_number_hi": house,
        "gender_hi": card["gender_hi"],
        "name_en": name_en,
        "relative_type_en": card["relative_type_en"],
        "relative_name_en": relative_en,
        "house_number_en": house_en,
        "gender_en": card["gender_en"],
        "address_hi": address_hi,
        "address_en": address_en,
        "age": card["age"],
    }


def extract(pdf_path: Path = PDF_PATH) -> list[dict]:
    ward = ward_from_filename(pdf_path)
    document = pymupdf.open(pdf_path)
    book = FontBook(document)
    rows = []
    seen = set()
    locality = ""
    for page in document:
        xref = hindi_font_xref(page)
        atoms = page_atoms(page, book, xref)
        cards, localities = parse_cards(atoms)
        locality = apply_locality(cards, localities, locality)
        for card in cards:
            if not card["serial_number"]:
                continue
            identity = (card["serial_number"], card["voter_id_number"])
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(build_row(card, ward))
    document.close()
    rows.sort(key=lambda row: int(row["serial_number"]))
    return rows


def write_csv(rows: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = extract()
    write_csv(rows)
    print(f"Records: {len(rows)}")
    print(f"CSV: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
