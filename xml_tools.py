"""
xml_tools.py — uniwersalny silnik konwersji XML (oferty hurtowni) -> CSV.

Nie zakłada z góry jednej konkretnej struktury (np. IOF 3.0 Chomika).
Zamiast tego:
  1. Wykrywa, który tag powtarza się jako pojedynczy produkt/oferta
     (detect_record_tag) - niezależnie czy nazywa się <product>, <item>,
     <produkt>, <oferta> itd.
  2. Przechodzi próbkę rekordów i buduje płaską listę WSZYSTKICH napotkanych
     pól (ścieżka -> przykładowa wartość) - discover_fields.
  3. Specjalnie rozpoznaje powtarzalny wzorzec "nazwa -> wartość"
     (typowy dla cech/parametrów produktu, niezależnie jak nazywają się
     tagi) i eksponuje każdą napotkaną nazwę cechy jako osobne, dynamiczne
     pole "param::<Nazwa>".
  4. Elementy z atrybutem xml:lang (typowe dla wielojęzycznych opisów) są
     rozróżniane jako osobne pola per język.
  5. Dwa pliki (np. pełny opis + osobny plik z cenami/stanami) można
     scalić po wspólnym identyfikatorze rekordu (zwykle atrybut @id).
  6. Wygenerowane wcześniej mapowanie (ścieżka -> nazwa kolumny CSV) jest
     stosowane do WSZYSTKICH rekordów (nie tylko próbki) przy generowaniu
     finalnego CSV.

Używane wyłącznie biblioteki standardowe (xml.etree.ElementTree, csv) -
zero dodatkowych zależności.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from collections import Counter

XML_LANG_ATTR = "{http://www.w3.org/XML/1998/namespace}lang"


class XmlParseError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _local_name(tag: str) -> str:
    """'{namespace}tag' -> 'tag'. Ignorujemy przestrzenie nazw przy
    dopasowywaniu tagów, żeby działać niezależnie od tego, czy XML w ogóle
    ich używa (część hurtowni ich nie deklaruje)."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_xml_bytes(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlParseError(f"Nie udało się wczytać pliku XML: {exc}") from exc


def detect_record_tag(root: ET.Element) -> str:
    """Heurystyka: szuka tagu, który najczęściej powtarza się jako
    bezpośrednie dziecko tego samego rodzica gdziekolwiek w drzewie -
    to zwykle węzeł pojedynczego produktu/oferty. Jeśli wszystko jest
    remisowe (np. plik z jednym produktem - każdy tag występuje raz),
    dogrywka: wygrywa tag "bogatszy" (więcej atrybutów + dzieci), bo to
    zwykle właściwy rekord produktu, a nie kontener w rodzaju <products>."""
    counts: Counter[str] = Counter()
    for parent in root.iter():
        child_tags = Counter(_local_name(c.tag) for c in parent)
        for tag, n in child_tags.items():
            counts[tag] = max(counts[tag], n)

    if not counts:
        return _local_name(root.tag)

    max_count = max(counts.values())
    candidates = {tag for tag, n in counts.items() if n == max_count}
    if len(candidates) == 1:
        return next(iter(candidates))

    best_tag, best_score = None, -1
    for parent in root.iter():
        for child in parent:
            tag = _local_name(child.tag)
            if tag not in candidates:
                continue
            score = len(child.attrib) + len(list(child))
            if score > best_score:
                best_score = score
                best_tag = tag
    return best_tag or next(iter(candidates))


def find_records(root: ET.Element, record_tag: str) -> list[ET.Element]:
    return [el for el in root.iter() if _local_name(el.tag) == record_tag]


def get_record_id(el: ET.Element) -> str | None:
    """Szuka unikalnego identyfikatora rekordu. 'id' jest zdecydowanie
    najczęstszą konwencją (tak jak w specyfikacji IOF), ale sprawdzamy też
    kilka typowych wariantów na wypadek innej hurtowni."""
    for attr in ("id", "ID", "Id", "code", "sku", "kod"):
        if attr in el.attrib:
            return el.attrib[attr]
    return None


def merge_records_by_id(
    records_a: list[ET.Element], records_b: list[ET.Element]
) -> tuple[list[ET.Element], int, int]:
    """Łączy rekordy z dwóch plików po identyfikatorze (np. full.xml +
    light.xml). Rekord z pliku A bez pary w B jest pomijany (i odwrotnie).
    Zwraca (scalone_rekordy, liczba_sparowanych, liczba_niesparowanych)."""
    by_id_b = {}
    for r in records_b:
        rid = get_record_id(r)
        if rid is not None:
            by_id_b[rid] = r

    merged: list[ET.Element] = []
    unmatched = 0
    for ra in records_a:
        rid = get_record_id(ra)
        rb = by_id_b.get(rid) if rid is not None else None
        if rb is None:
            unmatched += 1
            continue
        combo = ET.Element("merged_record", {**ra.attrib, **rb.attrib})
        for child in list(ra):
            combo.append(child)
        for child in list(rb):
            combo.append(child)
        merged.append(combo)

    return merged, len(merged), unmatched


PREFERRED_LABEL_ATTRS = ("name", "nazwa", "label", "etykieta", "value", "wartosc", "title", "tytul")
_ID_LIKE_ATTRS = {"id", "ID", "Id"}


def _detect_pair_attr(el: ET.Element) -> str | None:
    """Jeśli el pasuje do wzorca 'cecha -> wartość(ci)' (element i WSZYSTKIE
    jego dzieci - bez własnych dalszych dzieci - mają wspólny atrybut
    opisowy), zwraca nazwę tego atrybutu. Nie zakłada, że nazywa się
    akurat 'name' po angielsku - sprawdza listę typowych odpowiedników
    (nazwa, label, wartosc...), a w ostateczności dowolny wspólny atrybut
    inny niż identyfikator - żeby działać też dla nietypowego nazewnictwa
    innych hurtowni."""
    children = list(el)
    if not children or any(list(c) for c in children):
        return None

    common = set(el.attrib.keys())
    if not common:
        return None
    for child in children:
        common &= set(child.attrib.keys())
    common -= _ID_LIKE_ATTRS
    if not common:
        return None

    for preferred in PREFERRED_LABEL_ATTRS:
        if preferred in common:
            return preferred
    return sorted(common)[0]


def discover_fields(records: list[ET.Element], max_sample: int = 30) -> dict[str, dict]:
    """Zwraca {ścieżka: {"label": ..., "sample": ...}} dla wszystkich pól
    napotkanych w próbce rekordów. Kolejność wstawiania = kolejność
    napotkania (przydatne do stabilnego, przewidywalnego UI)."""
    fields: dict[str, dict] = {}

    def register(path: str, label: str, value: str) -> None:
        if not value:
            return
        if path not in fields:
            fields[path] = {"label": label, "sample": value}

    def walk(el: ET.Element, path: str) -> None:
        pair_attr = _detect_pair_attr(el)
        if pair_attr:
            param_name = el.attrib.get(pair_attr, "")
            values = []
            for child in el:
                cv = child.attrib.get(pair_attr)
                if cv and cv not in values:
                    values.append(cv)
            register(f"param::{param_name}", f"Cecha: {param_name}", "|".join(values))
            return  # nie schodzimy głębiej ani nie rejestrujemy surowych atrybutów - gałąź już w pełni obsłużona

        for attr, val in el.attrib.items():
            if attr == XML_LANG_ATTR:
                continue
            aname = _local_name(attr)
            p = f"{path}/@{aname}" if path else f"@{aname}"
            register(p, p, val)

        children = list(el)
        text = (el.text or "").strip()
        if text and not children and path:
            register(path, path, text)

        for child in children:
            tag = _local_name(child.tag)
            lang = child.attrib.get(XML_LANG_ATTR)
            seg = f"{tag}[xml:lang={lang}]" if lang else tag
            child_path = f"{path}/{seg}" if path else seg
            walk(child, child_path)

    for rec in records[:max_sample]:
        walk(rec, "")

    return fields


def _resolve_path(el: ET.Element, path: str) -> str:
    """Wyodrębnia wartość dla ścieżki w formacie generowanym przez
    discover_fields (np. 'description/name[xml:lang=pol]',
    '@code_on_card', 'param::Materiał')."""
    if path.startswith("param::"):
        return _extract_param_value(el, path[len("param::") :])

    if path.startswith("@"):
        return el.attrib.get(path[1:], "")

    current = [el]
    trailing_attr = None
    for seg in path.split("/"):
        if seg.startswith("@"):
            trailing_attr = seg[1:]
            break
        tag, cond = seg, None
        if "[" in seg and seg.endswith("]"):
            tag, rest = seg[:-1].split("[", 1)
            k, v = rest.split("=", 1)
            cond = (k, v)
        nxt = []
        for node in current:
            for child in node:
                if _local_name(child.tag) != tag:
                    continue
                if cond:
                    k, v = cond
                    actual = child.attrib.get(XML_LANG_ATTR) if k == "xml:lang" else child.attrib.get(k)
                    if actual != v:
                        continue
                nxt.append(child)
        current = nxt
        if not current:
            return ""

    if not current:
        return ""
    node = current[0]
    if trailing_attr:
        return node.attrib.get(trailing_attr, "")
    return (node.text or "").strip()


def _extract_param_value(el: ET.Element, param_name: str) -> str:
    values: list[str] = []
    for node in el.iter():
        pair_attr = _detect_pair_attr(node)
        if pair_attr and node.attrib.get(pair_attr) == param_name:
            for child in node:
                cv = child.attrib.get(pair_attr)
                if cv and cv not in values:
                    values.append(cv)
    return "|".join(values)


def generate_csv(records: list[ET.Element], mapping: dict[str, str]) -> str:
    """mapping: {ścieżka_pola: nazwa_kolumny_CSV}, w kolejności, w jakiej
    mają się pojawić kolumny. Separator ';' - spójnie z resztą aplikacji."""
    output = io.StringIO()
    fieldnames = list(mapping.values())
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for rec in records:
        row = {col: _resolve_path(rec, path) for path, col in mapping.items()}
        writer.writerow(row)
    return output.getvalue()


def inspect_xml_files(files: list[bytes]) -> dict:
    """Główna funkcja wysokopoziomowa: parsuje 1 lub 2 pliki XML, scala je
    (jeśli 2), wykrywa pola. Zwraca strukturę gotową do zserializowania
    jako JSON dla frontendu + listę sparsowanych rekordów (do dalszego
    użycia przy generowaniu CSV - trzymane w pamięci po stronie backendu,
    nie w tym słowniku)."""
    if not files or len(files) > 2:
        raise XmlParseError("Wgraj jeden lub dwa pliki XML.")

    roots = [parse_xml_bytes(data) for data in files]
    per_file_records = []
    for root in roots:
        tag = detect_record_tag(root)
        per_file_records.append(find_records(root, tag))

    matched = None
    unmatched = None
    if len(per_file_records) == 2:
        records, matched, unmatched = merge_records_by_id(per_file_records[0], per_file_records[1])
        if not records:
            raise XmlParseError(
                "Nie udało się dopasować żadnych rekordów między dwoma plikami po ich "
                "identyfikatorze (atrybut 'id'). Sprawdź, czy oba pliki dotyczą tego "
                "samego zestawu produktów, albo wgraj tylko jeden plik."
            )
    else:
        records = per_file_records[0]

    if not records:
        raise XmlParseError(
            "Nie znaleziono żadnych powtarzalnych rekordów produktów w tym pliku. "
            "Sprawdź, czy to na pewno plik z ofertą produktową."
        )

    fields = discover_fields(records)

    return {
        "total_records": len(records),
        "matched_records": matched,
        "unmatched_records": unmatched,
        "fields": fields,
        "_records": records,  # tylko do użytku wewnętrznego backendu
    }
