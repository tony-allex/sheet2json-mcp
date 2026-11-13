"""End-to-end tests: talk to the real MCP server through an in-process MCP client."""
import json
import shutil
from pathlib import Path

import pytest
from mcp import Client

from sheet2json.server import build_server

EXAMPLES = Path(__file__).parent.parent / "examples" / "data"


@pytest.fixture
def server(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(EXAMPLES, data)
    return build_server(data_dir=data, output_dir=tmp_path / "output")


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content


@pytest.mark.anyio
async def test_tools_are_listed(server):
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"list_schemas", "list_files", "inspect_file", "convert_file"}


@pytest.mark.anyio
async def test_inspect_then_convert_and_write(server, tmp_path):
    async with Client(server) as client:
        info = payload(await client.call_tool("inspect_file", {"path": "led_board_bom.xlsx"}))
        assert info["schema_suggestions"][0]["schema"] == "bom"

        result = payload(await client.call_tool(
            "convert_file", {"path": "led_board_bom.xlsx", "schema": "bom", "write_output": True}
        ))
    assert result["valid_rows"] == 2 and result["errors"] == []
    written = json.loads(Path(result["output_file"]).read_text(encoding="utf-8"))
    assert written["records"][1] == {"designators": "D1", "part_number": "LTST-C191KRKT",
                                     "manufacturer": "Lite-On", "quantity": 2, "unit_price_dkk": 0.9}


@pytest.mark.anyio
async def test_output_is_refused_when_rows_are_invalid(server, tmp_path):
    async with Client(server) as client:
        result = payload(await client.call_tool(
            "convert_file", {"path": "pcb_bom_rev_b.xlsx", "schema": "bom", "write_output": True}
        ))
    assert result["output_file"] is None
    assert not (tmp_path / "output").exists()


@pytest.mark.anyio
async def test_unknown_schema_and_outside_paths_are_errors(server):
    async with Client(server) as client:
        bad_schema = await client.call_tool("convert_file", {"path": "led_board_bom.xlsx", "schema": "invoice"})
        escape = await client.call_tool("inspect_file", {"path": "../../etc/passwd.csv"})
    assert bad_schema.is_error and "Available schemas" in bad_schema.content[0].text
    assert escape.is_error and "outside" in escape.content[0].text
