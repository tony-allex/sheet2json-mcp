"""Generate the example files in examples/data. Run: python examples/make_examples.py"""
from pathlib import Path
from openpyxl import Workbook

out = Path(__file__).parent / "data"
out.mkdir(exist_ok=True)

# A BOM exported by hand: inconsistent headers, an extra column, and two problems
wb = Workbook()
ws = wb.active
ws.title = "BOM"
ws.append(["Ref Des", "Part No.", "Mfr", "Description", "Value", "Footprint", "Qty", "Unit Price (DKK)", "Notes"])
rows = [
    ["R1, R2, R5", "RC0603FR-0710KL", "Yageo", "Resistor 10k 1%", "10k", "0603", 3, 0.12, ""],
    ["C1", "GRM188R71C104KA01D", "Murata", "Capacitor 100nF X7R", "100nF", "0603", 1, 0.35, ""],
    ["U1", "STM32L432KCU6", "STMicroelectronics", "MCU Cortex-M4", "", "UFQFPN-32", 1, 38.5, "low power"],
    ["U2", "BME280", "Bosch", "Temp/humidity/pressure sensor", "", "LGA-8", "one", 21.9, "qty typed as text"],
    ["J1", "", "Molex", "USB-C connector", "", "USB-C", 1, 6.2, "part number missing"],
    ["C1", "GRM188R71C104KA01D", "Murata", "Capacitor 100nF X7R", "100nF", "0603", 1, 0.35, "duplicate line"],
]
for r in rows:
    ws.append(r)
ws2 = wb.create_sheet("Notes")
ws2.append(["Rev B, exported from the old ordering spreadsheet"])
wb.save(out / "pcb_bom_rev_b.xlsx")

# A clean BOM, to show write_output working
wb = Workbook()
ws = wb.active
ws.append(["Designator", "MPN", "Manufacturer", "Qty", "Price"])
ws.append(["R1", "RC0603FR-0710KL", "Yageo", 1, 0.12])
ws.append(["D1", "LTST-C191KRKT", "Lite-On", 2, 0.9])
wb.save(out / "led_board_bom.xlsx")

# A Danish-style CSV: semicolon delimiter, decimal comma, dd-mm-yyyy dates
(out / "district_heating_log.csv").write_text(
    "Tidspunkt;Sensor ID;Type;Værdi;Enhed;Lokation\n"
    "13-11-2026 08:00;TS-101;temperature;72,5;°C;Substation A\n"
    "13-11-2026 08:00;PS-201;pressure;6,1;bar;Substation A\n"
    "13-11-2026 08:00;FS-301;flow;12,8;m3/h;Substation A\n"
    "13-11-2026 08:15;TS-101;Temperature;72,9;°C;Substation A\n"
    "13-11-2026 08:15;TS-102;temperature;n/a;°C;Substation B\n"
    "13-11-2026 08:15;PS-201;pressure;6,0;psi;Substation A\n",
    encoding="utf-8",
)
print("Examples written to", out)
