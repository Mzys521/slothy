"""Template export for md/txt/csv/json/docx data without executing code. /
以数据模板导出 md/txt/csv/json/docx，不执行生成代码。"""

import csv
import io
import json
import re
from pathlib import Path
from uuid import uuid4

from docx import Document

from app.config import ToolSettings
from app.tools.context import execution_context
from app.tools.contracts import ToolOperationError, ToolResult, artifact_for


class DocumentExporter:
    """
    Write bounded document files from plain data templates. / 使用纯数据模板写入有界文档文件。

    Outputs stay under this run's artifact directory; existing files are never
    overwritten and input size is capped before writing. /
    产物位于本次运行的产物目录；不覆盖已有文件，写入前限制输入大小。
    """

    def __init__(self, settings: ToolSettings):
        """
        Bind the exporter to tool storage settings. / 将导出器绑定到工具存储配置。

        Args:
            settings: Workspace root, artifact directory and byte limits. /
            工作区根目录、产物目录与字节上限。
        """
        self.settings = settings
        self.root = settings.workspace_path
        self.artifacts = settings.artifacts_path or self.root

    async def run(self, format: str, filename: str, title="", sections=None):
        """
        Export one structured document in the requested format. /
        按请求格式导出一个结构化文档。

        Args:
            format: One of md, txt, csv, json or docx. / md、txt、csv、json 或 docx 之一。
            filename: Plain filename whose extension matches the format. /
            扩展名与格式匹配的纯文件名。
            title: Optional document title. / 可选文档标题。
            sections: Ordered sections with headings, paragraphs, items and tables. /
                含标题、段落、条目与表格的有序章节。

        Returns:
            Serialized ToolResult carrying the generated artifact. / 含生成产物的序列化
            ToolResult。

        Raises:
            ToolOperationError: If the filename, input size, target or output size is invalid.
            /
                文件名、输入大小、目标或输出大小无效时抛出。
        """
        if (
            Path(filename).name != filename
            or filename in {".", ".."}
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
            or filename.endswith((".", " "))
            or filename.split(".")[0].casefold()
            in {
                "con",
                "prn",
                "aux",
                "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10)),
            }
            or Path(filename).suffix.lower() != "." + format
        ):
            raise ToolOperationError("INVALID_ARGS", "Use a plain filename matching the format.")
        sections = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in sections or []
        ]
        value = {"title": title, "sections": sections}
        if len(json.dumps(value, ensure_ascii=False)) > 100_000:
            raise ToolOperationError(
                "OUTPUT_TOO_LARGE", "Document input exceeds 100,000 characters."
            )
        context = execution_context.get()
        key = context.key if context else uuid4().hex
        output = self.artifacts / "runs" / key / "files"
        output.mkdir(parents=True, exist_ok=True)
        target = output / filename
        if target.exists():
            raise ToolOperationError(
                "RECOVERY_REQUIRED", "The output already exists; inspect it first."
            )
        if format == "docx":
            document = Document()
            if title:
                document.add_heading(title, 0)
            for section in sections:
                if section["heading"]:
                    document.add_heading(section["heading"], 1)
                for paragraph in section["paragraphs"]:
                    document.add_paragraph(paragraph)
                for item in section["items"]:
                    document.add_paragraph(item, style="List Bullet")
                if table := section.get("table"):
                    rendered = document.add_table(rows=1, cols=len(table["columns"]))
                    rendered.style = "Table Grid"
                    for cell, text in zip(rendered.rows[0].cells, table["columns"], strict=True):
                        cell.text = text
                    for row in table["rows"]:
                        for cell, text in zip(rendered.add_row().cells, row, strict=True):
                            cell.text = text
            with target.open("xb") as stream:
                document.save(stream)
        else:
            if format == "json":
                text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            elif format == "csv":
                stream = io.StringIO(newline="")
                writer = csv.writer(stream)
                if title:
                    writer.writerow([title])
                for section in sections:
                    if section["heading"]:
                        writer.writerow([section["heading"]])
                    for paragraph in [*section["paragraphs"], *section["items"]]:
                        writer.writerow([paragraph])
                    if table := section.get("table"):
                        writer.writerow(table["columns"])
                        writer.writerows(table["rows"])
                text = stream.getvalue()
            else:
                lines = [("# " if format == "md" else "") + title] if title else []
                for section in sections:
                    if section["heading"]:
                        lines.extend(["", ("## " if format == "md" else "") + section["heading"]])
                    lines.extend(section["paragraphs"])
                    lines.extend("- " + item for item in section["items"])
                    if table := section.get("table"):
                        rows = [table["columns"], *table["rows"]]
                        if format == "md":
                            rows.insert(1, ["---"] * len(table["columns"]))
                        lines.extend(
                            " | ".join(cell.replace("|", "\\|") for cell in row) for row in rows
                        )
                text = "\n".join(lines) + "\n"
            data = text.encode("utf-8")
            if len(data) > self.settings.max_artifact_bytes:
                raise ToolOperationError("OUTPUT_TOO_LARGE", "Document exceeds the artifact limit.")
            with target.open("xb") as stream:
                stream.write(data)
        if target.stat().st_size > self.settings.max_artifact_bytes:
            raise ToolOperationError("OUTPUT_TOO_LARGE", "Document exceeds the artifact limit.")
        return ToolResult(
            ok=True,
            summary=f"Exported {filename}.",
            data={"run_id": key},
            artifacts=[artifact_for(target, self.root, key)],
        ).model_dump()
