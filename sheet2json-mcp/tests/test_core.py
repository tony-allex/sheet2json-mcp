from pathlib import Path

import pytest

from sheet2json import core

EXAMPLES = Path(__file__).parent.parent / "examples" / "data"
SCHEMAS = core.load_schemas(Path(__file__).parent.parent / "src" / "sheet2json" / "schemas")


def convert(name, schema, **kw):
    table = core.read_table(EXAMPLES / name)
    return core.convert_table(table, SCHEMAS[schema], **kw)


def test_messy_bom_headers_are_mapped():
    result = convert("pcb_bom_rev_b.xlsx", "bom")
    assert result["column_mapping"]["designators"] == "Ref Des"
    assert result["column_mapping"]["part_number"] == "Part No."
    assert result["column_mapping"]["unit_price_dkk"] == "Unit Price (DKK)"
    assert result["unmapped_columns"] == ["Notes"]


def test_bad_rows_are_reported_not_dropped_silently():
    result = convert("pcb_bom_rev_b.xlsx", "bom")
    assert result["rows_read"] == 6
    assert result["valid_rows"] == 3
    assert result["invalid_rows"] == 3
    problems = {(e["row"], e["field"]) for e in result["errors"]}
    assert (5, "quantity") in problems          # "one" is not a number
    assert (6, "part_number") in problems       # required value missing
    assert (7, "designators") in problems       # duplicate of row 3


def test_types_are_converted():
    record = convert("pcb_bom_rev_b.xlsx", "bom")["records"][0]
    assert record["quantity"] == 3 and isinstance(record["quantity"], int)
    assert record["unit_price_dkk"] == pytest.approx(0.12)
    assert "notes" not in record


def test_danish_csv_with_decimal_comma():
    result = convert("district_heating_log.csv", "sensor_readings", decimal_separator=",")
    first = result["records"][0]
    assert first == {
        "timestamp": "2026-11-13T08:00:00",
        "sensor_id": "TS-101",
        "measurement": "temperature",
        "value": 72.5,
        "unit": "°C",
        "site": "Substation A",
    }
    # "Temperature" is matched case-insensitively and returned in canonical form
    assert result["records"][3]["measurement"] == "temperature"
    fields = {e["field"] for e in result["errors"]}
    assert fields == {"value", "unit"}  # "n/a" and "psi" are rejected


@pytest.mark.parametrize(
    "raw, sep, expected",
    [("1,234.5", ".", 1234.5), ("1.234,5", ",", 1234.5), ("72,5", ",", 72.5), ("  10 ", ".", 10.0)],
)
def test_number_parsing(raw, sep, expected):
    assert core.coerce(raw, {"type": "number"}, sep) == expected


@pytest.mark.parametrize("raw, sep", [("3,3", "."), ("12.34.5", ",")])
def test_ambiguous_numbers_are_rejected(raw, sep):
    with pytest.raises(core.CoercionError):
        core.coerce(raw, {"type": "number"}, sep)


def test_missing_required_column_stops_conversion():
    table = core.Table(headers=["Sensor", "Value"], rows=[(2, ["A", 1])])
    with pytest.raises(core.Sheet2JsonError, match="Required columns missing"):
        core.convert_table(table, SCHEMAS["sensor_readings"])


def test_path_traversal_is_blocked(tmp_path):
    with pytest.raises(core.Sheet2JsonError, match="outside"):
        core.resolve_inside(tmp_path, "../secret.csv")


def test_schema_suggestion_ranks_best_match_first():
    table = core.read_table(EXAMPLES / "pcb_bom_rev_b.xlsx")
    assert core.suggest_schema(table.headers, SCHEMAS)[0]["schema"] == "bom"
