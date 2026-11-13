"""Core conversion logic: read a spreadsheet, map it onto a fixed schema, validate.

This module has no MCP dependency so it can be tested and reused on its own.
The MCP server in ``server.py`` is a thin layer on top of it.

Design rule ("on rails"): the converter never guesses silently. Every value is
either converted according to the schema, or reported back as an error with its
row number and column. Nothing is dropped or "fixed" without being reported.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xlsm"}
DEFAULT_MAX_ROWS = 5000

TRUE_WORDS = {"true", "yes", "y", "1", "ja", "x"}
FALSE_WORDS = {"false", "no", "n", "0", "nej", ""}
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y")
DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M",
)


class Sheet2JsonError(Exception):
    """An error the caller (or the AI agent) should see and act on."""


# --------------------------------------------------------------------------- #
# Safe file access
# --------------------------------------------------------------------------- #

def resolve_inside(base_dir: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` inside ``base_dir`` and refuse anything outside it.

    This is the main guard rail: the agent can only read files from the
    configured data folder, never e.g. ``../../.ssh/id_rsa``.
    """
    base = base_dir.resolve()
    candidate = (base / relative_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise Sheet2JsonError(f"Path '{relative_path}' is outside the allowed data folder.")
    return candidate


def list_data_files(data_dir: Path) -> list[dict[str, Any]]:
    data_dir = data_dir.resolve()
    files = []
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES and not path.name.startswith("~$"):
            files.append({
                "path": path.relative_to(data_dir).as_posix(),
                "type": path.suffix.lower().lstrip("."),
                "size_kb": round(path.stat().st_size / 1024, 1),
            })
    return files


# --------------------------------------------------------------------------- #
# Reading tables
# --------------------------------------------------------------------------- #

@dataclass
class Table:
    headers: list[str]
    rows: list[tuple[int, list[Any]]]  # (row number as the user sees it, cell values)
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    truncated: bool = False


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def read_table(path: Path, sheet: str | None = None, max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise Sheet2JsonError(f"Unsupported file type '{suffix}'. Supported: {sorted(SUPPORTED_SUFFIXES)}")
    if not path.is_file():
        raise Sheet2JsonError(f"File not found: {path.name}")
    if suffix == ".csv":
        return _read_csv(path, max_rows)
    return _read_excel(path, sheet, max_rows)


def _read_csv(path: Path, max_rows: int) -> Table:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise Sheet2JsonError("Could not decode CSV file (tried UTF-8 and Windows-1252).")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    return _build_table(((i, row) for i, row in enumerate(reader, start=1)), max_rows)


def _read_excel(path: Path, sheet: str | None, max_rows: int) -> Table:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = list(workbook.sheetnames)
        name = sheet or sheets[0]
        if name not in sheets:
            raise Sheet2JsonError(f"Sheet '{name}' not found. Available sheets: {sheets}")
        rows = ((i, list(r)) for i, r in enumerate(workbook[name].iter_rows(values_only=True), start=1))
        table = _build_table(rows, max_rows)
    finally:
        workbook.close()
    table.sheet, table.sheets = name, sheets
    return table


def _build_table(numbered_rows, max_rows: int) -> Table:
    headers: list[str] | None = None
    rows: list[tuple[int, list[Any]]] = []
    truncated = False
    for number, values in numbered_rows:
        if all(_is_empty(v) for v in values):
            continue
        if headers is None:  # first non-empty row is the header row
            headers = ["" if v is None else str(v).strip() for v in values]
            continue
        if len(rows) >= max_rows:
            truncated = True
            break
        rows.append((number, list(values)))
    if headers is None:
        raise Sheet2JsonError("The file is empty: no header row found.")
    return Table(headers=headers, rows=rows, truncated=truncated)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

def normalise(name: str) -> str:
    """'Part No.' -> 'part_no', 'Unit Price (DKK)' -> 'unit_price_dkk'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass
class Schema:
    name: str
    raw: dict[str, Any]

    @property
    def properties(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("properties", {})

    @property
    def required(self) -> list[str]:
        return list(self.raw.get("required", []))

    @property
    def unique_key(self) -> list[str]:
        return list(self.raw.get("x-unique-key", []))

    def aliases(self, field_name: str) -> set[str]:
        prop = self.properties[field_name]
        return {normalise(field_name)} | {normalise(a) for a in prop.get("x-aliases", [])}

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.raw.get("title", self.name),
            "description": self.raw.get("description", ""),
            "fields": {
                k: {
                    "type": v.get("type"),
                    "required": k in self.required,
                    **({"enum": v["enum"]} if "enum" in v else {}),
                    **({"format": v["format"]} if "format" in v else {}),
                }
                for k, v in self.properties.items()
            },
        }


def load_schemas(schema_dir: Path) -> dict[str, Schema]:
    schemas: dict[str, Schema] = {}
    for path in sorted(schema_dir.glob("*.schema.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(raw)  # refuse broken schemas at start-up
        name = path.name.removesuffix(".schema.json")
        schemas[name] = Schema(name=name, raw=raw)
    return schemas


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #

@dataclass
class ColumnMapping:
    field_to_column: dict[str, int]
    unmapped_columns: list[str]
    missing_required: list[str]

    @property
    def score(self) -> float:
        return len(self.field_to_column)


def map_columns(headers: list[str], schema: Schema) -> ColumnMapping:
    normalised = [normalise(h) for h in headers]
    field_to_column: dict[str, int] = {}
    used: set[int] = set()
    for field_name in schema.properties:
        names = schema.aliases(field_name)
        for index, header in enumerate(normalised):
            if index not in used and header in names:
                field_to_column[field_name] = index
                used.add(index)
                break
    unmapped = [h for i, h in enumerate(headers) if i not in used and h]
    missing = [f for f in schema.required if f not in field_to_column]
    return ColumnMapping(field_to_column, unmapped, missing)


def suggest_schema(headers: list[str], schemas: dict[str, Schema]) -> list[dict[str, Any]]:
    """Rank schemas by how well the headers match. Purely informational."""
    ranking = []
    for schema in schemas.values():
        mapping = map_columns(headers, schema)
        ranking.append({
            "schema": schema.name,
            "matched_fields": len(mapping.field_to_column),
            "missing_required": mapping.missing_required,
        })
    ranking.sort(key=lambda r: (len(r["missing_required"]), -r["matched_fields"]))
    return ranking


# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #

class CoercionError(ValueError):
    pass


def _parse_number(value: Any, decimal_separator: str) -> float:
    if isinstance(value, bool):
        raise CoercionError("boolean is not a number")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    thousands, decimal = (".", ",") if decimal_separator == "," else (",", ".")
    if thousands in text:
        # Only accept the thousands separator in real groups of three (1,234,567.8).
        # Anything else, such as "3,3" in a file read with '.', is ambiguous: report it.
        grouped = rf"^-?\d{{1,3}}(\{thousands}\d{{3}})+(\{decimal}\d+)?$"
        if not re.match(grouped, text):
            raise CoercionError(
                f"'{value}' is ambiguous with decimal separator '{decimal_separator}'"
            )
        text = text.replace(thousands, "")
    text = text.replace(decimal, ".")
    try:
        return float(text)
    except ValueError:
        raise CoercionError(f"'{value}' is not a number") from None


def coerce(value: Any, prop: dict[str, Any], decimal_separator: str = ".") -> Any:
    """Convert one cell to the type the schema asks for, or raise CoercionError."""
    kind = prop.get("type", "string")
    fmt = prop.get("format")

    if kind == "integer":
        number = _parse_number(value, decimal_separator)
        if not number.is_integer():
            raise CoercionError(f"'{value}' is not a whole number")
        return int(number)

    if kind == "number":
        return _parse_number(value, decimal_separator)

    if kind == "boolean":
        if isinstance(value, bool):
            return value
        word = str(value).strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise CoercionError(f"'{value}' is not a yes/no value")

    # strings, dates and enums
    if fmt == "date":
        return _parse_datetime(value, DATE_FORMATS).date().isoformat()
    if fmt == "date-time":
        return _parse_datetime(value, DATETIME_FORMATS).isoformat()

    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel stores 1234 as 1234.0
    text = str(value).strip()

    if "enum" in prop:  # case-insensitive match, but always return the canonical value
        for option in prop["enum"]:
            if text.lower() == str(option).lower():
                return option
        raise CoercionError(f"'{text}' is not one of {prop['enum']}")
    return text


def _parse_datetime(value: Any, formats: tuple[str, ...]) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    text = str(value).strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    for f in formats:
        try:
            return dt.datetime.strptime(text, f)
        except ValueError:
            continue
    raise CoercionError(f"'{text}' is not a recognised date")


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

def convert_table(table: Table, schema: Schema, decimal_separator: str = ".") -> dict[str, Any]:
    if decimal_separator not in {".", ","}:
        raise Sheet2JsonError("decimal_separator must be '.' or ','")

    mapping = map_columns(table.headers, schema)
    if mapping.missing_required:
        raise Sheet2JsonError(
            f"Required columns missing for schema '{schema.name}': {mapping.missing_required}. "
            f"Columns in file: {table.headers}. No data was converted."
        )

    validator = Draft202012Validator(schema.raw, format_checker=FormatChecker())
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_keys: dict[tuple, int] = {}

    for row_number, values in table.rows:
        record: dict[str, Any] = {}
        row_errors: list[dict[str, Any]] = []

        for field_name, column in mapping.field_to_column.items():
            cell = values[column] if column < len(values) else None
            if _is_empty(cell):
                continue  # missing required values are caught by the validator below
            try:
                record[field_name] = coerce(cell, schema.properties[field_name], decimal_separator)
            except CoercionError as exc:
                row_errors.append({"row": row_number, "field": field_name, "value": str(cell), "message": str(exc)})

        if not row_errors:
            for err in validator.iter_errors(record):
                field_name = err.path[0] if err.path else _field_from_required_error(err)
                row_errors.append({"row": row_number, "field": field_name, "message": err.message})

        if not row_errors and schema.unique_key:
            key = tuple(record.get(k) for k in schema.unique_key)
            if key in seen_keys:
                row_errors.append({
                    "row": row_number,
                    "field": ",".join(schema.unique_key),
                    "message": f"Duplicate of row {seen_keys[key]} ({dict(zip(schema.unique_key, key))})",
                })
            else:
                seen_keys[key] = row_number

        if row_errors:
            errors.extend(row_errors)
        else:
            records.append(record)

    return {
        "schema": schema.name,
        "sheet": table.sheet,
        "rows_read": len(table.rows),
        "valid_rows": len(records),
        "invalid_rows": len({e["row"] for e in errors}),
        "truncated": table.truncated,
        "column_mapping": {f: table.headers[c] for f, c in mapping.field_to_column.items()},
        "unmapped_columns": mapping.unmapped_columns,
        "errors": errors,
        "records": records,
    }


def _field_from_required_error(err) -> str | None:
    match = re.match(r"'(.+?)' is a required property", err.message)
    return match.group(1) if match else None
