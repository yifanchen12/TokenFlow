"""Small, dependency-light document extraction for text, DOCX, XLSX and PDF."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".json", ".csv", ".tsv", ".docx", ".xlsx", ".pdf"}
MAX_ZIP_ENTRIES = 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_TOTAL_XML_BYTES = 64 * 1024 * 1024


class DocumentParseError(ValueError):
    pass


def _read_xml(archive: zipfile.ZipFile, name: str, remaining: int) -> bytes:
    limit = min(MAX_XML_BYTES, remaining)
    if archive.getinfo(name).file_size > limit:
        raise DocumentParseError("文档解压后超过大小限制")
    with archive.open(name) as source:
        xml = source.read(limit + 1)
    if len(xml) > limit:
        raise DocumentParseError("文档解压后超过大小限制")
    return xml


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_ZIP_ENTRIES:
                raise DocumentParseError("文档内文件数量超过限制")
            xml = _read_xml(archive, "word/document.xml", MAX_TOTAL_XML_BYTES)
        root = ElementTree.fromstring(xml)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise DocumentParseError("DOCX 结构无效") from exc
    values = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return " ".join(values).strip()


def _xlsx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_ZIP_ENTRIES:
                raise DocumentParseError("文档内文件数量超过限制")
            shared: list[str] = []
            remaining = MAX_TOTAL_XML_BYTES
            if "xl/sharedStrings.xml" in archive.namelist():
                xml = _read_xml(archive, "xl/sharedStrings.xml", remaining)
                remaining -= len(xml)
                root = ElementTree.fromstring(xml)
                for item in root:
                    shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
            rows: list[str] = []
            for name in sorted(item for item in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item)):
                xml = _read_xml(archive, name, remaining)
                remaining -= len(xml)
                root = ElementTree.fromstring(xml)
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
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
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
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="w"><w:t>hello</w:t></w:document>')
    assert parse_document_bytes("a.docx", buffer.getvalue())["text"] == "hello"
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        try:
            _read_xml(archive, "word/document.xml", 4)
        except DocumentParseError:
            pass
        else:
            raise AssertionError("oversized XML must fail")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="s"><row><c><v>42</v></c></row></worksheet>')
    assert parse_document_bytes("a.xlsx", buffer.getvalue())["text"] == "42"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(MAX_ZIP_ENTRIES + 1):
            archive.writestr(f"item{index}", "")
    try:
        parse_document_bytes("a.xlsx", buffer.getvalue())
    except DocumentParseError:
        pass
    else:
        raise AssertionError("excessive ZIP entries must fail")
