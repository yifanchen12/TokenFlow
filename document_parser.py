"""Small, dependency-light document extraction for text, DOCX, XLSX and PDF."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".json", ".csv", ".tsv", ".docx", ".xlsx", ".pdf"}


class DocumentParseError(ValueError):
    pass


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (KeyError, OSError, ElementTree.ParseError) as exc:
        raise DocumentParseError("DOCX 结构无效") from exc
    values = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return " ".join(values).strip()


def _xlsx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root:
                    shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
            rows: list[str] = []
            for name in sorted(item for item in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item)):
                root = ElementTree.fromstring(archive.read(name))
                for row in root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    cells: list[str] = []
                    for cell in row:
                        if not cell.tag.endswith("}c"):
                            continue
                        value = next((node.text or "" for node in cell if node.tag.endswith("}v")), "")
                        cell_type = cell.attrib.get("t")
                        if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        cells.append(value)
                    if cells:
                        rows.append("\t".join(cells))
            return "\n".join(rows).strip()
    except (KeyError, OSError, ElementTree.ParseError) as exc:
        raise DocumentParseError("XLSX 结构无效") from exc


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except ImportError as exc:
        raise DocumentParseError("PDF 解析需要可选依赖 pypdf") from exc
    except Exception as exc:
        raise DocumentParseError("PDF 文本提取失败") from exc


def parse_document_bytes(name: str, data: bytes) -> dict[str, Any]:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParseError(f"不支持的文件类型：{suffix or '无扩展名'}")
    if suffix == ".docx":
        text = _docx(data)
    elif suffix == ".xlsx":
        text = _xlsx(data)
    elif suffix == ".pdf":
        text = _pdf(data)
    else:
        text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise DocumentParseError("文件未提取出文本")
    return {"name": Path(name).name, "extension": suffix, "text": text, "characters": len(text)}


def parse_document_file(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise DocumentParseError("文件不存在")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise DocumentParseError("文件超过 20 MB 限制")
    return parse_document_bytes(path.name, path.read_bytes())


def self_check() -> None:
    assert ".docx" in SUPPORTED_EXTENSIONS
    assert parse_document_bytes("a.txt", b"hello")["text"] == "hello"
