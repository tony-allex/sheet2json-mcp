"""sheet2json MCP server.

Exposes four tools that let an AI agent turn messy spreadsheets into clean,
validated JSON, but only "on rails":

* it can only read files inside SHEET2JSON_DATA_DIR,
* it can only convert to schemas that already exist in SHEET2JSON_SCHEMA_DIR,
* every rejected value is reported with row number and reason,
* output is written to SHEET2JSON_OUTPUT_DIR only when every row is valid.

Run with:  sheet2json-mcp            (stdio transport, for Claude Desktop / Claude Code)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import core

PACKAGE_SCHEMAS = Path(__file__).parent / "schemas"
PREVIEW_RECORDS = 20  # keep tool responses small; the full data goes to the output file
MAX_ERRORS_SHOWN = 50


def _config() -> tuple[Path, Path, Path]:
    data_dir = Path(os.environ.get("SHEET2JSON_DATA_DIR", "./data"))
    output_dir = Path(os.environ.get("SHEET2JSON_OUTPUT_DIR", "./output"))
    schema_dir = Path(os.environ.get("SHEET2JSON_SCHEMA_DIR", PACKAGE_SCHEMAS))
    return data_dir, output_dir, schema_dir


def build_server(
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    schema_dir: Path | None = None,
) -> MCPServer:
    env_data, env_output, env_schema = _config()
    data_dir = (data_dir or env_data).resolve()
    output_dir = (output_dir or env_output).resolve()
    schemas = core.load_schemas(schema_dir or env_schema)
    if not schemas:
        raise RuntimeError("No schemas found. Add *.schema.json files to the schema folder.")

    server = MCPServer(
        name="sheet2json",
        version="0.1.0",
        instructions=(
            "Converts spreadsheets (CSV/XLSX) into validated JSON using fixed schemas. "
            "Workflow: list_files -> inspect_file -> convert_file. Never invent a schema; "
            "if no schema fits, tell the user. Report conversion errors to the user "
            "instead of correcting data yourself."
        ),
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def _schema(name: str) -> core.Schema:
        if name not in schemas:
            raise ToolError(f"Unknown schema '{name}'. Available schemas: {sorted(schemas)}")
        return schemas[name]

    def _table(path: str, sheet: str | None) -> tuple[Path, core.Table]:
        try:
            file_path = core.resolve_inside(data_dir, path)
            return file_path, core.read_table(file_path, sheet)
        except core.Sheet2JsonError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=read_only)
    def list_schemas() -> dict[str, Any]:
        """List the target schemas data can be converted into, with their fields and which are required."""
        return {"schemas": [s.summary() for s in schemas.values()]}

    @server.tool(annotations=read_only)
    def list_files() -> dict[str, Any]:
        """List the CSV and Excel files available in the data folder."""
        return {"data_folder": str(data_dir), "files": core.list_data_files(data_dir)}

    @server.tool(annotations=read_only)
    def inspect_file(path: str, sheet: str | None = None, sample_rows: int = 5) -> dict[str, Any]:
        """Show the sheets, column headers and first rows of a file, and which schemas fit it best.

        Args:
            path: File path relative to the data folder, as returned by list_files.
            sheet: Excel sheet name. Defaults to the first sheet. Ignored for CSV.
            sample_rows: Number of example rows to return (1-20).
        """
        _, table = _table(path, sheet)
        sample_rows = max(1, min(sample_rows, 20))
        sample = [
            {"row": number, **{h or f"column_{i + 1}": _jsonable(v) for i, (h, v) in enumerate(zip(table.headers, values))}}
            for number, values in table.rows[:sample_rows]
        ]
        return {
            "path": path,
            "sheets": table.sheets,
            "sheet": table.sheet,
            "headers": table.headers,
            "row_count": len(table.rows),
            "sample": sample,
            "schema_suggestions": core.suggest_schema(table.headers, schemas),
        }

    @server.tool()
    def convert_file(
        path: str,
        schema: str,
        sheet: str | None = None,
        decimal_separator: Literal[".", ","] = ".",
        write_output: bool = False,
    ) -> dict[str, Any]:
        """Convert a spreadsheet into JSON records that match one of the predefined schemas.

        Every row is either converted and validated, or listed in `errors` with its row
        number and reason. Rows are never silently dropped or corrected.

        Args:
            path: File path relative to the data folder.
            schema: Name of the target schema (see list_schemas).
            sheet: Excel sheet name. Defaults to the first sheet.
            decimal_separator: "." (1,234.5) or "," (1.234,5 - common in Danish files).
            write_output: Save the result as JSON in the output folder. Only allowed when
                there are no errors, so partial data never ends up downstream.
        """
        target = _schema(schema)
        file_path, table = _table(path, sheet)
        try:
            result = core.convert_table(table, target, decimal_separator)
        except core.Sheet2JsonError as exc:
            raise ToolError(str(exc)) from exc

        response: dict[str, Any] = {k: v for k, v in result.items() if k not in {"records", "errors"}}
        response["errors"] = result["errors"][:MAX_ERRORS_SHOWN]
        response["errors_truncated"] = len(result["errors"]) > MAX_ERRORS_SHOWN
        response["records_preview"] = result["records"][:PREVIEW_RECORDS]

        if write_output:
            if result["errors"] or result["truncated"]:
                response["output_file"] = None
                response["output_note"] = "Not written: fix the reported errors first (or the file exceeded the row limit)."
            else:
                output_dir.mkdir(parents=True, exist_ok=True)
                out = output_dir / f"{file_path.stem}.{target.name}.json"
                payload = {"source": path, "schema": target.name, "sheet": table.sheet, "records": result["records"]}
                out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                response["output_file"] = str(out)
        return response

    return server


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
