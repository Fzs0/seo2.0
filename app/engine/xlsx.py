"""轻量 XLSX 解析（不依赖 openpyxl）。"""
from __future__ import annotations

import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xlsx_buffer_to_rows(buf: bytes, sheet_name: str | None = None) -> list[list[str]]:
    """从 .xlsx 读取指定工作表的二维字符串数组；不指定时保持读取 sheet1。"""
    with zipfile.ZipFile(io := __import__("io").BytesIO(buf)) as z:
        try:
            shared = ET.fromstring(z.read("xl/sharedStrings.xml"))
        except KeyError:
            shared = None
        sheet_path = _sheet_path(z, sheet_name)
        if not sheet_path:
            return []
        sheet = ET.fromstring(z.read(sheet_path))
        strings = ["".join(t.text or "" for t in si.findall(".//main:t", NS)) for si in shared.findall("main:si", NS)] if shared is not None else []
        rows: list[list[str]] = []
        for row in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: list[str] = []
            last_col = 0
            for c in row.findall("main:c", NS):
                ref = c.get("r", "")
                col = _col_index(ref)
                t = c.get("t")
                v = c.find("main:v", NS)
                if t == "inlineStr":
                    text = "".join(node.text or "" for node in c.findall(".//main:t", NS))
                elif v is None:
                    text = ""
                elif t == "s":
                    text = strings[int(v.text)] if v.text and strings else ""
                else:
                    text = v.text or ""
                while last_col < col - 1:
                    cells.append("")
                    last_col += 1
                cells.append(text)
                last_col = col
            if cells:
                rows.append(cells)
        return rows


def _sheet_path(z: zipfile.ZipFile, sheet_name: str | None) -> str | None:
    if not sheet_name:
        return "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in z.namelist() else None
    try:
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        relationships = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return None
    rel_map = {item.get("Id"): item.get("Target") for item in relationships}
    relation_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        if sheet.get("name", "").strip().casefold() != sheet_name.strip().casefold():
            continue
        target = rel_map.get(sheet.get(relation_key))
        if not target:
            return None
        return posixpath.normpath(posixpath.join("xl", target.lstrip("/")))
    return None


def _col_index(ref: str) -> int:
    """A → 1, B → 2, AA → 27。"""
    m = re.match(r"([A-Z]+)", ref)
    if not m:
        return 1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n
