"""
Extract bounded text from files explicitly supplied to a conversation. /
从会话显式提供的文件中提取有界文本。
"""

from io import BytesIO
from pathlib import PurePath
from posixpath import normpath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_THREAD_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_CONTEXT_CHARS = 60_000
MAX_PREVIEW_CHARS = 60_000
MAX_ATTACHMENTS = 8
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".xml",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".sql",
    ".toml",
    ".ini",
}
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx"})
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".ppt"})
DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
RELATION = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_PDF_PAGES = 80
MAX_PDF_CHARS = 200_000


def _read_pdf(contents: bytes) -> str:
    """Extract bounded text from a PDF with pypdf. / 使用 pypdf 提取有界 PDF 文本。

    pypdf raises many document-specific exception types for damaged, empty or unsupported
    files; they are all normalized into ``ValueError`` so the API can answer with one stable
    error contract. / pypdf 会对损坏、空白或不支持的文件抛出多种文档异常；此处统一归一化为
    ``ValueError``，使 API 能用同一错误契约回答。

    Args:
        contents: Raw PDF bytes, already bounded by the upload limit. / 已限制大小的 PDF 字节。

    Returns:
        Extracted text. / 提取的文本。

    Raises:
        ValueError: When the PDF is encrypted, malformed or has no text.
            / PDF 加密、损坏或无文本时抛出。
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(contents))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF files are unsupported.")
        parts = []
        remaining = MAX_PDF_CHARS
        for index in range(min(len(reader.pages), MAX_PDF_PAGES)):
            text = (reader.pages[index].extract_text() or "").replace("\x00", "")
            if not text.strip():
                continue
            part = f"Page {index + 1}\n{text}"[:remaining]
            parts.append(part)
            remaining -= len(part) + 1
            if remaining <= 0:
                break
        if not parts:
            raise ValueError("The PDF has no selectable text. Apply OCR to scanned pages first.")
        return "\n".join(parts)
    except ValueError:
        raise
    except Exception as error:
        # Includes PdfReadError, PdfStreamError and malformed-xref failures from pypdf.
        # 包括 pypdf 的 PdfReadError、PdfStreamError 与损坏的交叉引用表错误。
        raise ValueError("Could not read the PDF document.") from error


def _office_xml(archive: ZipFile, name: str) -> ElementTree.Element:
    """Parse a bounded XML part without processing document declarations."""
    part = archive.getinfo(name)
    if part.file_size > MAX_UPLOAD_BYTES:
        raise ValueError("The document exceeds the XML read limit.")
    xml = archive.read(part)
    declarations = xml.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("Document declarations are unsupported.")
    return ElementTree.fromstring(xml)


def _paragraphs(root: ElementTree.Element, namespace: str) -> str:
    return "\n".join(
        "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        for paragraph in root.iter(namespace + "p")
    )


def _read_powerpoint(archive: ZipFile) -> str:
    """Follow the deck's slide order and extract text, including table cells."""
    presentation = _office_xml(archive, "ppt/presentation.xml")
    namespace = presentation.tag.partition("}")[0] + "}"
    relationships = _office_xml(archive, "ppt/_rels/presentation.xml.rels")
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships
        if item.get("Type", "").endswith("/slide") and item.get("TargetMode") != "External"
    }
    slides = presentation.findall(f"./{namespace}sldIdLst/{namespace}sldId")
    parts = []
    remaining_xml = MAX_UPLOAD_BYTES
    for number, slide in enumerate(slides, start=1):
        target = targets[slide.attrib[RELATION + "id"]]
        path = normpath(target[1:] if target.startswith("/") else f"ppt/{target}")
        remaining_xml -= archive.getinfo(path).file_size
        if remaining_xml < 0:
            raise ValueError("The PowerPoint slides exceed the XML read limit.")
        text = _paragraphs(_office_xml(archive, path), DRAWING)
        if text.strip():
            parts.append(f"Slide {number}\n{text}")
    return "\n\n".join(parts)


def read_attachment(filename: str, contents: bytes) -> str:
    """
    Extract plain text from UTF-8, PDF, Word or PowerPoint. /
    从 UTF-8 文本、PDF、Word 或 PowerPoint 中提取正文。

    Args:
        filename: Uploaded filename whose extension chooses the reader. /
        上传文件名，由其扩展名选择读取方式。
        contents: Raw uploaded bytes, already bounded by the caller. /
        已由调用方限制大小的原始上传字节。

    Returns:
        Decoded non-empty text without NUL bytes. / 不含 NUL 字节的非空解码文本。

    Raises:
        ValueError: If the type is unsupported, unsafe or unreadable. /
            类型不支持、不安全或不可读时抛出。
    """
    extension = PurePath(filename).suffix.lower()
    if extension in LEGACY_OFFICE_EXTENSIONS:
        raise ValueError("Save legacy .doc or .ppt files as .docx or .pptx first.")
    if extension not in TEXT_EXTENSIONS and extension not in DOCUMENT_EXTENSIONS:
        raise ValueError("Unsupported file type. Use UTF-8 text, PDF, DOCX or PPTX.")
    try:
        if extension == ".pdf":
            text = _read_pdf(contents)
        elif extension in {".docx", ".pptx"}:
            with ZipFile(BytesIO(contents)) as archive:
                if extension == ".docx":
                    root = _office_xml(archive, "word/document.xml")
                    text = _paragraphs(root, root.tag.partition("}")[0] + "}")
                else:
                    text = _read_powerpoint(archive)
        else:
            text = contents.decode("utf-8-sig")
    except (
        UnicodeDecodeError,
        BadZipFile,
        KeyError,
        ElementTree.ParseError,
        RuntimeError,
    ) as error:
        raise ValueError(f"Could not read the {extension[1:].upper()} file.") from error
    if not text.strip() or "\x00" in text:
        raise ValueError("The file has no readable text.")
    return text


def attachment_references(
    documents: list[dict], *, max_chars: int = MAX_CONTEXT_CHARS
) -> list[dict]:
    """
    Share the prompt budget across uploaded files. / 在上传文件之间分配提示词预算。

    Args:
        documents: Uploaded document mappings with id, filename and content. /
            含 id、filename 与 content 的上传文档映射。
        max_chars: Shared character budget for preview text. / 多个附件共享的预览字符预算。

    Returns:
        Reference dicts with bounded text and next_offset when truncated. /
        含截断游标的参考字典列表。
    """
    remaining = max_chars
    references = {}
    for index, item in enumerate(sorted(documents, key=lambda doc: len(doc["content"]))):
        total = item.get("total_chars", len(item["content"]))
        end = min(len(item["content"]), remaining // (len(documents) - index))
        remaining -= end
        references[item["id"]] = {
            "id": item["id"],
            "filename": item["filename"],
            "text": item["content"][:end],
            "total_chars": total,
            "next_offset": end if end < total else None,
        }
    return [references[item["id"]] for item in documents]
