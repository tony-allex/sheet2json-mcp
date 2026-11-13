---
name: sheet2json
description: Convert CSV or Excel files (BOMs, sensor logs) into validated JSON with the sheet2json MCP tools. Use when the user wants spreadsheet data cleaned, converted, imported or checked against a schema.
---

# Converting spreadsheets with sheet2json

The sheet2json MCP server does the conversion. Your job is to drive it in a fixed
order and report honestly what happened. Do not convert data yourself in the chat,
and do not "repair" values the server rejected.

## Workflow

1. **Find the file.** Call `list_files`. If the user's file is not listed, ask them to
   put it in the data folder. Do not try other paths.
2. **Inspect it.** Call `inspect_file` with the path. Look at `headers`, `sample` and
   `schema_suggestions`.
3. **Choose the schema.**
   - Use the top suggestion only if its `missing_required` list is empty.
   - If no schema fits, stop and tell the user which required columns are missing.
     Never invent a new schema or map columns by hand.
   - If two schemas fit equally well, ask the user which one they mean.
4. **Pick the decimal separator.** Look at the sample values. If numbers look like
   `72,5` or `1.234,5` (common in Danish files), pass `decimal_separator: ","`.
   Otherwise use the default `"."`.
5. **Convert without writing.** Call `convert_file` with `write_output: false` first.
6. **Report the result** to the user in this shape:
   - rows read, valid rows, invalid rows
   - the column mapping, and any `unmapped_columns` (data that will not be included)
   - every error as: row number, field, value, reason (group repeated errors)
7. **Write the output only when it is clean.** If there are no errors and the user
   wants the file, call `convert_file` again with `write_output: true` and give them
   the `output_file` path. If there are errors, the server will refuse to write; ask
   the user to fix the source file and run it again.

## Rules

- Never change, guess or fill in values that the server rejected, even if the fix
  looks obvious (for example `"one"` → `1`). The user decides.
- Never claim a conversion succeeded unless `invalid_rows` is 0.
- If `truncated` is true, say that the file exceeded the row limit and only part of
  it was checked.
- If a tool returns an error, show the message to the user and stop. Do not retry
  with a different path or schema unless the user asks.

## Example report

> Converted `pcb_bom_rev_b.xlsx` (sheet "BOM") to the **bom** schema.
> 6 rows read, 3 valid, 3 need fixing:
> - Row 5, quantity: "one" is not a number
> - Row 6, part_number: missing
> - Row 7, designators: duplicate of row 3 (C1)
>
> The "Notes" column is not part of the schema and was left out.
> Fix these rows and I'll run it again, then save the JSON.
