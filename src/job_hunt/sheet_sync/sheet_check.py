"""Reads a tab back from Google and compares it with the layout it should
have, so a broken sheet is caught by the run, not by the user."""

import gspread

from job_hunt.sheet_sync.sheet_format import FROZEN_COLUMNS, body_row_pixels
from job_hunt.sheet_sync.sheet_rows import KEY_COLUMN, LINK_COLUMN, SheetColumn

# rows read back per tab: enough to catch a column-wide problem
SAMPLED_ROWS = 40


def check_tab(worksheet: gspread.Worksheet, columns: list[SheetColumn]) -> list[str]:
    title = worksheet.title
    last_column = gspread.utils.rowcol_to_a1(1, len(columns)).rstrip("1")
    metadata = worksheet.spreadsheet.fetch_sheet_metadata(
        {
            "ranges": f"'{title}'!A1:{last_column}{SAMPLED_ROWS + 1}",
            "includeGridData": True,
            "fields": "sheets(properties.gridProperties,data(columnMetadata(pixelSize,hiddenByUser),"
            "rowMetadata.pixelSize,rowData.values(formattedValue,hyperlink,dataValidation,"
            "userEnteredFormat(horizontalAlignment,wrapStrategy))))",
        }
    )
    sheet = metadata["sheets"][0]
    grid = sheet["properties"]["gridProperties"]
    data = sheet["data"][0]
    rows = [row.get("values", []) for row in data.get("rowData", [])]
    problems: list[str] = []

    headers = [cell.get("formattedValue", "") for cell in rows[0]] if rows else []
    expected_headers = [column.header for column in columns]
    if headers != expected_headers:
        return [f"{title}: headers are {headers}, expected {expected_headers}"]
    if grid.get("frozenRowCount") != 1 or grid.get("frozenColumnCount") != FROZEN_COLUMNS:
        problems.append(
            f"{title}: frozen panes are {grid.get('frozenRowCount')} rows / "
            f"{grid.get('frozenColumnCount')} columns"
        )

    expected_height = body_row_pixels(columns)
    heights = [row.get("pixelSize") for row in data.get("rowMetadata", [])[1:]]
    if any(height != expected_height for height in heights):
        problems.append(f"{title}: rows are {sorted(set(heights))}px tall, expected {expected_height}")
    for index, (column, metadata_of_column) in enumerate(
        zip(columns, data.get("columnMetadata", []), strict=False)
    ):
        if metadata_of_column.get("pixelSize") != column.width:
            problems.append(
                f"{title}: '{column.header}' is {metadata_of_column.get('pixelSize')}px wide, "
                f"expected {column.width}"
            )
        if bool(metadata_of_column.get("hiddenByUser")) != (column.header == KEY_COLUMN):
            problems.append(
                f"{title}: '{column.header}' hidden is {bool(metadata_of_column.get('hiddenByUser'))}"
            )
        expected_alignment = "CENTER" if column.centred else "LEFT"
        for row_number, row in enumerate(rows[1:], start=2):
            cell = row[index] if index < len(row) else {}
            alignment = cell.get("userEnteredFormat", {}).get("horizontalAlignment")
            if alignment != expected_alignment:
                problems.append(
                    f"{title}: '{column.header}' row {row_number} is aligned {alignment}, "
                    f"expected {expected_alignment}"
                )
                break
            wrap = cell.get("userEnteredFormat", {}).get("wrapStrategy")
            if wrap != ("WRAP" if column.wraps else "CLIP"):
                problems.append(f"{title}: '{column.header}' row {row_number} wraps as {wrap}")
                break
            if ("dataValidation" in cell) != bool(column.choices):
                problems.append(
                    f"{title}: '{column.header}' row {row_number} dropdown is {'dataValidation' in cell}"
                )
                break
            if cell.get("hyperlink") and column.header != LINK_COLUMN:
                problems.append(f"{title}: '{column.header}' row {row_number} carries a link")
                break
            if column.header == LINK_COLUMN and cell.get("formattedValue") and not cell.get("hyperlink"):
                problems.append(f"{title}: advert link in row {row_number} isn't clickable")
                break
    return problems


# Every row's verdict, not a sample: a wrong row on Jobs is the one defect
# that matters most.
def check_verdicts(worksheet: gspread.Worksheet, allowed: set[str]) -> list[str]:
    header = worksheet.row_values(1)
    if "Verdict" not in header:
        return [f"{worksheet.title}: no Verdict column"]
    values = worksheet.col_values(header.index("Verdict") + 1)[1:]
    wrong = sorted({value for value in values if value and value not in allowed})
    return [f"{worksheet.title}: has rows with verdict {wrong}, allowed {sorted(allowed)}"] if wrong else []


# Every value written to a column with fixed values must be one of them, or
# the cell's dropdown flags it; the user's own columns may hold anything.
def check_choices(title: str, rows: list[list[str]], columns: list[SheetColumn]) -> list[str]:
    problems = []
    for index, column in enumerate(columns):
        if not column.choices or column.belongs_to_user:
            continue
        stray = sorted({row[index] for row in rows[1:] if row[index] and row[index] not in column.choices})
        if stray:
            problems.append(f"{title}: '{column.header}' has values outside its dropdown: {stray}")
    return problems


def check_tab_order(spreadsheet: gspread.Spreadsheet, expected: list[str]) -> list[str]:
    titles = [worksheet.title for worksheet in spreadsheet.worksheets()]
    return (
        []
        if titles[: len(expected)] == expected
        else [f"tabs are in the order {titles}, expected {expected}"]
    )
