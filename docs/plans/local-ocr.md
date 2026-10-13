# Local OCR (2026-10-12)

The user requested local OCR appropriate for this machine and authorized implementation.

- Verified hardware: i7-14700HX, 32 GB RAM, RTX 4060 Laptop 8 GB. Installed
  RapidOCR 1.2.3 (ONNX Runtime) already includes Chinese/English model files;
  Pillow and PyMuPDF are available. Reuse these optional dependencies, keep CPU
  inference so OCR does not reserve GPU memory needed by other local models.
- `local_ocr.py`: lazy, serialized CPU OCR, local model status, image size limits,
  text/confidence metadata, no network or cloud OCR. PNG/JPEG/WebP/BMP supported.
- `document_parser.py`: image extraction; PDF text per page first, rasterize and
  OCR only empty pages, preserving mixed text/scanned page order. Existing text,
  DOCX/XLSX and size contracts retained. Cap scan rendering work and report limits.
- `tokenflow.py`, `ui_page.py`: expose OCR status and file acceptance, show the
  extraction method before submitting recognized text to task models.
- Build scripts: include installed OCR configs/models in the packaged executable.
- Validate a real Chinese screenshot, a generated scan PDF and a mixed PDF;
  fixture tests cover no-text/unsupported/size/optional dependency failures.

Order: verify OCR module -> parser assembly -> local API/UI -> EXE/restart smoke.
