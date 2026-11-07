"""
Extract bounded text from files explicitly supplied to a conversation. /
从会话显式提供的文件中提取有界文本。
"""

from io import BytesIO
from pathlib import PurePath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_CHARS = 60_000
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
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_attachment(filename: str, contents: bytes) -> str:
    """
    Decode one uploaded text or DOCX file into plain text. / 将上传的文本或 DOCX
    文件解码为纯文本。

    Args:
        filename: Uploaded filename whose extension chooses the reader. /
        上传文件名，由其扩展名选择读取方式。
        contents: Raw uploaded bytes, already bounded by the caller. /
        已由调用方限制大小的原始上传字节。

    Returns:
        Decoded non-empty text without NUL bytes. / 不含 NUL 字节的非空解码文本。

    Raises:
        ValueError: If the type is unsupported, unsafe or not readable UTF-8/DOCX text. /
            类型不支持、不安全或不是可读 UTF-8/DOCX 文本时抛出。
    """
    extension = PurePath(filename).suffix.lower()
    if extension not in TEXT_EXTENSIONS and extension != ".docx":
        raise ValueError("Unsupported file type. Upload UTF-8 text or a DOCX document.")
    try:
        if extension == ".docx":
            with ZipFile(BytesIO(contents)) as archive:
                document = archive.getinfo("word/document.xml")
                if document.file_size > MAX_UPLOAD_BYTES:
                    raise ValueError("The document is too large to read.")
                xml = archive.read(document)
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ValueError("Document declarations are unsupported.")
            root = ElementTree.fromstring(xml.decode("utf-8-sig"))
            text = "\n".join(
                "".join(node.text or "" for node in paragraph.iter(WORD + "t"))
                for paragraph in root.iter(WORD + "p")
            )
        else:
            text = contents.decode("utf-8-sig")
    except (
        UnicodeDecodeError,
        BadZipFile,
        KeyError,
        ElementTree.ParseError,
        RuntimeError,
    ) as error:
        raise ValueError("Could not read the document as UTF-8 text or DOCX.") from error
    if not text.strip() or "\x00" in text:
        raise ValueError("The file has no readable text.")
    return text


def attachment_references(documents: list[dict]) -> list[dict]:
    """
    Share the prompt budget across uploaded files. / 在上传文件之间分配提示词预算。

    Args:
        documents: Uploaded document mappings with id, filename and content. /
            含 id、filename 与 content 的上传文档映射。

    Returns:
        Reference dicts with bounded text and next_offset when truncated. /
        含截断游标的参考字典列表。
    """
    remaining = MAX_CONTEXT_CHARS
    references = {}
    for index, item in enumerate(sorted(documents, key=lambda doc: len(doc["content"]))):
        total = len(item["content"])
        end = min(total, remaining // (len(documents) - index))
        remaining -= end
        references[item["id"]] = {
            "id": item["id"],
            "filename": item["filename"],
            "text": item["content"][:end],
            "total_chars": total,
            "next_offset": end if end < total else None,
        }
    return [references[item["id"]] for item in documents]
