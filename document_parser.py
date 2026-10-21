"""Small, dependency-light document extraction for text, DOCX, XLSX and PDF."""

from __future__ import annotations

import io
import csv
import hashlib
import importlib.metadata
import sqlite3
import time
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".json", ".csv", ".tsv", ".docx", ".xlsx", ".pdf"} | IMAGE_EXTENSIONS
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
    def paragraph(node):
        return "".join(child.text or "" if child.tag.endswith("}t") else
                       "\t" if child.tag.endswith("}tab") else "\n" if child.tag.endswith(("}br", "}cr")) else ""
                       for child in node.iter())
    paragraphs = [paragraph(node) for node in root.iter() if node.tag.endswith("}p")]
    return "\n\n".join(paragraphs).strip() if paragraphs else paragraph(root).strip()


def _xlsx(data: bytes) -> dict:
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
            names = archive.namelist()
            sheets = []
            if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
                xml = _read_xml(archive, "xl/_rels/workbook.xml.rels", remaining)
                remaining -= len(xml)
                relationships = {r.attrib.get("Id"): posixpath.normpath(
                    r.attrib.get("Target", "").lstrip("/") if r.attrib.get("Target", "").startswith("/")
                    else "xl/" + r.attrib.get("Target", ""))
                    for r in ElementTree.fromstring(xml) if r.attrib.get("TargetMode") != "External"}
                xml = _read_xml(archive, "xl/workbook.xml", remaining)
                remaining -= len(xml)
                for sheet in ElementTree.fromstring(xml).iter():
                    if sheet.tag.endswith("}sheet"):
                        identity = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), "")
                        target = relationships.get(identity, "")
                        if not target.startswith("xl/worksheets/") or target not in names:
                            raise DocumentParseError("XLSX 工作表引用无效")
                        sheets.append((sheet.attrib.get("name", "Sheet"), target))
            else:
                sheets = [(Path(n).stem, n) for n in sorted(
                    (n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                    key=lambda n: int(re.search(r"sheet(\d+)", n).group(1)))]
            extracted, total_cells, warnings, missing_formula_count = [], 0, [], 0
            for title, name in sheets:
                xml = _read_xml(archive, name, remaining)
                remaining -= len(xml)
                root = ElementTree.fromstring(xml)
                rows, width = [], 0
                for row in root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    row_number = int(row.attrib.get("r", len(rows) + 1))
                    if row_number <= len(rows) or row_number > 100001:
                        raise DocumentParseError("XLSX 行位置无效或超过 100001 行限制")
                    cells: list[str] = []
                    for cell in row:
                        if not cell.tag.endswith("}c"):
                            continue
                        reference = cell.attrib.get("r", "")
                        match = re.fullmatch(r"([A-Z]+)(\d+)", reference) if reference else None
                        if reference and (not match or int(match.group(2)) != row_number):
                            raise DocumentParseError("XLSX 单元格位置无效")
                        column = 0
                        if match:
                            for letter in match.group(1):
                                column = column * 26 + ord(letter) - ord("A") + 1
                        else:
                            column = len(cells) + 1
                        if column <= len(cells) or column > 16384:
                            raise DocumentParseError("XLSX 列位置无效")
                        value = next((node.text or "" for node in cell if node.tag.endswith("}v")), "")
                        cell_type = cell.attrib.get("t")
                        if cell_type == "s":
                            if not value.isdigit() or int(value) >= len(shared):
                                raise DocumentParseError("XLSX 共享字符串引用无效")
                            value = shared[int(value)]
                        elif cell_type == "inlineStr":
                            value = "".join(n.text or "" for n in cell.iter() if n.tag.endswith("}t"))
                        formula = next((n.text or "" for n in cell if n.tag.endswith("}f")), None)
                        if formula is not None:
                            if not value:
                                value = "=" + formula + " [无缓存值]"
                                missing_formula_count += 1
                            warnings.append("公式使用文件内缓存值，未重新计算；无缓存值时保留公式")
                        cells.extend([""] * (column - len(cells) - 1))
                        cells.append(value)
                    rows.extend([[] for _ in range(row_number - len(rows) - 1)])
                    rows.append(cells)
                    width = max(width, len(cells))
                    if total_cells + len(rows) * width > 1_000_000:
                        raise DocumentParseError("XLSX 展开后超过 100 万单元格限制")
                values = [r + [""] * (width - len(r)) for r in rows]
                total_cells += len(values) * width
                output = io.StringIO()
                csv.writer(output, delimiter="\t", lineterminator="\n").writerows(values)
                extracted.append({"name": title, "text": output.getvalue().rstrip("\n"),
                                  "rows": len(values), "columns": width})
            text = "\n\n".join(("[工作表：" + s["name"] + "]\n" if len(extracted) > 1 else "") + s["text"] for s in extracted)
            return {"text": text, "sheets": extracted, "warnings": list(dict.fromkeys(warnings)),
                    "missing_formula_count": missing_formula_count}
    except DocumentParseError:
        raise
    except (ValueError, KeyError, OSError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise DocumentParseError("XLSX 结构无效") from exc


def _pdf(data: bytes) -> dict:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        scanned = [i for i, text in enumerate(pages) if not text.strip()]
        metadata = {"method": "pdf_text", "pages": len(pages), "ocr_pages": [], "warnings": [], "ocr_details": []}
        try:
            import fitz
        except ImportError as exc:
            if scanned:
                raise DocumentParseError("扫描 PDF 需要本地 OCR 及可选依赖 PyMuPDF") from exc
            metadata["warnings"].append("未安装 PyMuPDF，未检查混合 PDF 中的图片正文")
        else:
            from local_ocr import recognize, OCRError
            with fitz.open(stream=data, filetype="pdf") as pdf:
                for index, page in enumerate(pdf):
                    if index in scanned:
                        continue
                    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
                    for item in page.get_image_info():
                        region = fitz.Rect(item["bbox"]) & page.rect
                        if region.get_area() < page.rect.get_area() * .05:
                            continue  # Small logos do not justify another recognition pass.
                        existing = sum(len(b[4].strip()) for b in blocks if
                                       (fitz.Rect(b[:4]) & region).get_area() > fitz.Rect(b[:4]).get_area() * .5)
                        if existing < 20:
                            scanned.append(index)
                            break
                if len(scanned) > 30:
                    raise DocumentParseError("需 OCR 的 PDF 最多 30 页，请拆分后处理")
                for index in scanned:
                    page = pdf[index]
                    zoom = min(2, 3000 / max(page.rect.width, page.rect.height))
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                    try:
                        recognized = recognize(pix.tobytes("png"))
                        original = pages[index].splitlines()
                        seen = {re.sub(r"\s+", "", line).casefold() for line in original}
                        added = []
                        for line in recognized["text"].splitlines():
                            key = re.sub(r"\s+", "", line).casefold()
                            if key and key not in seen:
                                added.append(line)
                                seen.add(key)
                        pages[index] = "\n".join(original + added)
                        metadata["ocr_details"].append({"page": index + 1, "mean_confidence": recognized.get("mean_confidence"),
                                                        "added_lines": len(added)})
                    except OCRError as exc:
                        # Blank pages stay blank; unavailable OCR never silently loses a scan.
                        if "未识别出文字" not in str(exc):
                            raise DocumentParseError(f"第 {index + 1} 页：{exc}") from exc
                        metadata["warnings"].append(f"第 {index + 1} 页图像未识别出文字，请核对原页")
                    metadata["ocr_pages"].append(index + 1)
            if scanned:
                metadata["method"] = "pdf_text_and_local_ocr"
        return {"text": "\n".join(pages).strip(), **metadata}
    except ImportError as exc:
        raise DocumentParseError("PDF 解析需要可选依赖 pypdf") from exc
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("PDF 文本提取失败") from exc


PARSER_CACHE_VERSION = 2


def parse_document_bytes(name: str, data: bytes, *, use_cache: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    cache, key = None, ""
    suffix = Path(name).suffix.lower()
    if use_cache:
        from tokenflow_store import DocumentStore
        versions = []
        for dependency in (("rapidocr_onnxruntime", "onnxruntime", "Pillow", "PyMuPDF", "pypdf") if suffix == ".pdf" else
                           ("rapidocr_onnxruntime", "onnxruntime", "Pillow") if suffix in IMAGE_EXTENSIONS else ()):
            try:
                versions.append(importlib.metadata.version(dependency))
            except importlib.metadata.PackageNotFoundError:
                versions.append("unavailable")
        key = hashlib.sha256(str((PARSER_CACHE_VERSION, suffix, versions)).encode() + b"\0" + data).hexdigest()
        try:
            cache = DocumentStore()
            from runtime_config import RuntimeConfig
            policy = RuntimeConfig().settings()
            if policy["cache_max_mb"] or policy["cache_retention_days"]:
                cache.manage(max_mb=policy["cache_max_mb"], days=policy["cache_retention_days"])
            saved = cache.parsed(key)
            if saved:
                return {**saved, "name": Path(name).name, "cache_reused": True,
                        "parse_duration_ms": round((time.monotonic() - started) * 1000)}
        except (OSError, sqlite3.Error, ValueError):
            cache = None
    extracted = _extract_document_bytes(name, data)
    if cache is not None:
        try:
            cache.parsed(key, extracted)
            if policy["cache_max_mb"] or policy["cache_retention_days"]:
                cache.manage(max_mb=policy["cache_max_mb"], days=policy["cache_retention_days"])
        except (OSError, sqlite3.Error):
            pass  # Cache is optional; successful extraction remains usable.
    return {**extracted, "cache_reused": False, "parse_duration_ms": round((time.monotonic() - started) * 1000)}


def _extract_document_bytes(name: str, data: bytes) -> dict[str, Any]:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParseError(f"不支持的文件类型：{suffix or '无扩展名'}")
    metadata = {"method": "document_text"}
    if suffix in IMAGE_EXTENSIONS:
        from local_ocr import recognize, OCRError
        try:
            extracted = recognize(data)
        except OCRError as exc:
            raise DocumentParseError(str(exc)) from exc
        text = extracted.pop("text")
        metadata = extracted
    elif suffix == ".docx":
        text = _docx(data)
    elif suffix == ".xlsx":
        extracted = _xlsx(data)
        text = extracted.pop("text")
        metadata.update(extracted)
    elif suffix == ".pdf":
        metadata = _pdf(data)
        text = metadata.pop("text")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise DocumentParseError("文件未提取出文本")
    return {"name": Path(name).name, "extension": suffix, "text": text, "characters": len(text), **metadata}


def parse_document_file(path_value: str, *, use_cache: bool = False) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise DocumentParseError("文件不存在")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise DocumentParseError("文件超过 20 MB 限制")
    return parse_document_bytes(path.name, path.read_bytes(), use_cache=use_cache)


def self_check() -> None:
    from unittest.mock import patch
    from local_ocr import OCRError
    assert ".docx" in SUPPORTED_EXTENSIONS
    with patch("local_ocr.recognize", return_value={"text": "OCR 42", "method": "local_ocr", "device": "CPU"}):
        extracted = parse_document_bytes("a.png", b"fixture")
        assert extracted["text"] == "OCR 42" and extracted["method"] == "local_ocr"
    with patch("local_ocr.recognize", side_effect=OCRError("图片未识别出文字")):
        try:
            parse_document_bytes("blank.png", b"fixture")
        except DocumentParseError:
            pass
        else:
            raise AssertionError("empty OCR must not silently succeed")
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
