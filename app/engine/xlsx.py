"""轻量 XLSX 解析（不依赖 openpyxl）。读 ZIP 中的 sheet1.xml 与 sharedStrings.xml。"""
from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xlsx_buffer_to_rows(buf: bytes) -> list[list[str]]:
    """从 .xlsx 的 bytes 读出 sheet1 的二维字符串数组。空 cell 用 '' 占位。"""
    with zipfile.ZipFile(io := __import__("io").BytesIO(buf)) as z:
        try:
            shared = ET.fromstring(z.read("xl/sharedStrings.xml"))
        except KeyError:
            shared = None
        try:
            sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        except KeyError:
            return []
        strings = [si.find("main:t", NS).text if si.find("main:t", NS) is not None else "" for si in shared.findall("main:si", NS)] if shared is not None else []
        rows: list[list[str]] = []
        for row in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: list[str] = []
            last_col = 0
            for c in row.findall("main:c", NS):
                ref = c.get("r", "")
                col = _col_index(ref)
                t = c.get("t")
                v = c.find("main:v", NS)
                if v is None:
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


def _col_index(ref: str) -> int:
    """A → 1, B → 2, AA → 27。"""
    m = re.match(r"([A-Z]+)", ref)
    if not m:
        return 1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n