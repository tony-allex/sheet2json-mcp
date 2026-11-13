# sheet2json-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent turn messy
spreadsheets into clean, validated JSON, **on rails**.

A lot of engineering and operations data still lives in hand-maintained Excel and
CSV files: bills of materials, sensor logs, order lists. Asking an LLM to "just convert
it" works until it quietly guesses a value, drops a row or invents a column. This
server keeps the AI useful but constrained: the agent decides *which* file and *which*
schema; deterministic Python code does the conversion and reports every problem.

```
"Convert pcb_bom_rev_b.xlsx to JSON"
        │
        ▼
  AI agent ──(MCP)──► list_files → inspect_file → convert_file
                                                  │
                     fixed JSON Schemas ──────────┤
                                                  ▼
                       valid records + row-level error report
                       (JSON file written only if every row is valid)
```

## The rails

| Guard rail | Why |
|---|---|
| Files can only be read from one data folder (path traversal is blocked) | The agent can't wander the file system |
| Only predefined JSON Schemas can be targeted | Output shape is always the same; the agent can't invent fields |
| Every rejected value is reported with row, field, value and reason | Nothing is dropped or "fixed" silently |
| Ambiguous numbers such as `3,3` with `.` as decimal separator are rejected | No silent 3.3 → 33 mistakes |
| Duplicate keys (e.g. the same designator twice) are flagged | Catches copy-paste errors in BOMs |
| Output is written only when **all** rows are valid | Partial data never reaches downstream systems |
| Tool responses return a preview (20 records, 50 errors) | Keeps the agent's context small; full data goes to the file |
| A [skill file](skills/sheet2json/SKILL.md) tells the agent the exact workflow and how to report | Consistent behaviour across conversations |

## What it handles

- `.xlsx`, `.xlsm` (any sheet) and `.csv` (auto-detects `,` `;` tab `|` delimiters, UTF-8 or Windows-1252)
- Messy headers: `Part No.`, `MPN` and `part_number` all map to the same field, using aliases defined in the schema
- Danish formats: decimal comma (`72,5`), `dd-mm-yyyy` dates, Danish column names (`Værdi`, `Enhed`, `Antal`)
- Type conversion to integer, number, boolean, enum (case-insensitive, returned in canonical form), date and date-time

## Tools

| Tool | What it does |
|---|---|
| `list_schemas` | Lists the target schemas, their fields and which are required |
| `list_files` | Lists CSV/Excel files in the data folder |
| `inspect_file` | Shows sheets, headers, sample rows and which schema fits best |
| `convert_file` | Converts and validates; optionally writes the JSON when there are no errors |

## Included schemas

- **`bom`**: PCB bill of materials (designators, part number, manufacturer, quantity, unit price…)
- **`sensor_readings`**: time-stamped temperature / pressure / flow / humidity readings, e.g. from a district heating substation

Adding a schema means dropping a `*.schema.json` file (standard JSON Schema 2020-12) into
the schema folder. Two extra keys are supported: `x-aliases` on a property (alternative
column names) and `x-unique-key` on the schema (fields that must be unique per row).

## Example

`examples/data/pcb_bom_rev_b.xlsx` has inconsistent headers and three bad rows. Result of
`convert_file(path="pcb_bom_rev_b.xlsx", schema="bom")` (shortened):

```json
{
  "rows_read": 6, "valid_rows": 3, "invalid_rows": 3,
  "column_mapping": { "designators": "Ref Des", "part_number": "Part No.", "quantity": "Qty", "...": "..." },
  "unmapped_columns": ["Notes"],
  "errors": [
    { "row": 5, "field": "quantity", "value": "one", "message": "'one' is not a number" },
    { "row": 6, "field": "part_number", "message": "'part_number' is a required property" },
    { "row": 7, "field": "designators", "message": "Duplicate of row 3 ({'designators': 'C1'})" }
  ],
  "records_preview": [ { "designators": "R1, R2, R5", "part_number": "RC0603FR-0710KL", "quantity": 3, "unit_price_dkk": 0.12, "...": "..." } ]
}
```

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/<tony-allex>/sheet2json-mcp.git
cd sheet2json-mcp
pip install -e ".[dev]"
pytest            # 17 tests, including end-to-end tests through a real MCP client
```

### Use with Claude Code

```bash
claude mcp add sheet2json \
  --env SHEET2JSON_DATA_DIR=/path/to/your/spreadsheets \
  --env SHEET2JSON_OUTPUT_DIR=/path/to/output \
  -- sheet2json-mcp
```

Copy `skills/sheet2json/` into your skills folder (e.g. `.claude/skills/`) so the agent follows the workflow.

### Use with Claude Desktop (or any MCP client)

```json
{
  "mcpServers": {
    "sheet2json": {
      "command": "sheet2json-mcp",
      "env": {
        "SHEET2JSON_DATA_DIR": "/path/to/your/spreadsheets",
        "SHEET2JSON_OUTPUT_DIR": "/path/to/output"
      }
    }
  }
}
```

| Environment variable | Default | Meaning |
|---|---|---|
| `SHEET2JSON_DATA_DIR` | `./data` | The only folder files can be read from |
| `SHEET2JSON_OUTPUT_DIR` | `./output` | Where validated JSON is written |
| `SHEET2JSON_SCHEMA_DIR` | built-in `schemas/` | Folder with `*.schema.json` files |

Try it on the examples by pointing `SHEET2JSON_DATA_DIR` at `examples/data`
(regenerate them with `python examples/make_examples.py`).

## Project layout

```
src/sheet2json/core.py      conversion logic (no MCP dependency, easy to test and reuse)
src/sheet2json/server.py    MCP tools: a thin layer over core.py
src/sheet2json/schemas/     JSON Schemas (the "rails")
skills/sheet2json/SKILL.md  instructions for the AI agent
tests/                      unit tests + end-to-end MCP tests
```

## Ideas for next steps

- A `write_to_database` tool (e.g. MongoDB or Azure Cosmos DB) behind the same validation
- Deploy over streamable HTTP in a container (Azure Container Apps) so a team can share one server
- A schema for component data that checks part numbers against a supplier API
