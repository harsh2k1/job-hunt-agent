"""How a tab looks: column widths, a dark header, clipped fixed-height
rows, banding, filters, frozen panes, a coloured Fit column and a Status
dropdown. Applied in one batch request per sync; anything a previous sync
added (colour rules, banding) is removed first, so nothing piles up."""

from typing import Any

import gspread

from job_hunt.sheet_sync.sheet_rows import KEY_COLUMN, LINK_COLUMN, STATUS_CHOICES, SheetColumn

HEADER_BACKGROUND = {"red": 0.17, "green": 0.24, "blue": 0.36}
WHITE = {"red": 1, "green": 1, "blue": 1}
BAND_GREY = {"red": 0.96, "green": 0.97, "blue": 0.98}
VERDICT_COLOURS = {
    "apply": {"red": 0.72, "green": 0.88, "blue": 0.75},
    "check": {"red": 1.0, "green": 0.95, "blue": 0.8},
    "not screened yet": {"red": 0.93, "green": 0.93, "blue": 0.93},
}
HEADER_ROW_PIXELS = 34
BODY_ROW_PIXELS = 24
# a tab with a wrapping column shows up to four lines of it per row
WRAPPED_ROW_PIXELS = 72
# #, Role, Company and Status stay in view while scrolling across
FROZEN_COLUMNS = 4


def format_tab(worksheet: gspread.Worksheet, columns: list[SheetColumn], rows: list[list[str]]) -> None:
    sheet_id = worksheet.id
    row_count = len(rows)
    requests = _removals(worksheet) + [
        # every cell back to plain: a format or dropdown an earlier layout
        # put on a column must not stay behind on whatever moved there
        _reset_all_cells(sheet_id, row_count, len(columns)),
        _frozen_panes(sheet_id),
        _row_heights(sheet_id, 0, 1, HEADER_ROW_PIXELS),
        _row_heights(sheet_id, 1, row_count, body_row_pixels(columns)),
        _cells(sheet_id, 0, 1, 0, len(columns), _header_format()),
        _cells(sheet_id, 1, row_count, 0, len(columns), _body_format()),
        _banding(sheet_id, row_count, len(columns)),
        {"setBasicFilter": {"filter": {"range": _range(sheet_id, 0, row_count, 0, len(columns))}}},
    ]
    for index, column in enumerate(columns):
        requests.append(_column_width(sheet_id, index, column.width, hidden=column.header == KEY_COLUMN))
        layout = {
            "horizontalAlignment": "CENTER" if column.centred else "LEFT",
            "wrapStrategy": "WRAP" if column.wraps else "CLIP",
        }
        requests.append(_cells(sheet_id, 1, row_count, index, index + 1, layout))
    headers = [column.header for column in columns]
    if "Verdict" in headers:
        requests += _verdict_colours(sheet_id, headers.index("Verdict"), row_count)
    if LINK_COLUMN in headers:
        requests.append(_clickable_links(sheet_id, headers.index(LINK_COLUMN), rows))
    if "Status" in headers:
        requests.append(_status_dropdown(sheet_id, headers.index("Status"), row_count))
    worksheet.spreadsheet.batch_update({"requests": requests})


# what earlier syncs added to this tab, so it's replaced rather than stacked
def _removals(worksheet: gspread.Worksheet) -> list[dict[str, Any]]:
    metadata = worksheet.spreadsheet.fetch_sheet_metadata(
        {"fields": "sheets(properties.sheetId,conditionalFormats,bandedRanges)"}
    )
    sheet = next(sheet for sheet in metadata["sheets"] if sheet["properties"]["sheetId"] == worksheet.id)
    removals: list[dict[str, Any]] = [
        {"deleteConditionalFormatRule": {"sheetId": worksheet.id, "index": 0}}
        for _ in sheet.get("conditionalFormats", [])
    ]
    removals += [
        {"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}} for band in sheet.get("bandedRanges", [])
    ]
    return removals


def _reset_all_cells(sheet_id: int, row_count: int, column_count: int) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": _range(sheet_id, 0, row_count, 0, column_count),
            "cell": {},
            "fields": "userEnteredFormat,dataValidation",
        }
    }


def _range(sheet_id: int, first_row: int, end_row: int, first_column: int, end_column: int) -> dict[str, int]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": first_row,
        "endRowIndex": end_row,
        "startColumnIndex": first_column,
        "endColumnIndex": end_column,
    }


def _cells(
    sheet_id: int,
    first_row: int,
    end_row: int,
    first_column: int,
    end_column: int,
    cell_format: dict,
    fields: str = "",
) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": _range(sheet_id, first_row, end_row, first_column, end_column),
            "cell": {"userEnteredFormat": cell_format},
            "fields": "userEnteredFormat(" + (fields or ",".join(cell_format)) + ")",
        }
    }


def _header_format() -> dict[str, Any]:
    return {
        "backgroundColor": HEADER_BACKGROUND,
        "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }


# long text is clipped to the column (full text on click), so rows stay one line
def _body_format() -> dict[str, Any]:
    return {"textFormat": {"fontSize": 10}, "verticalAlignment": "TOP"}


def body_row_pixels(columns: list[SheetColumn]) -> int:
    return WRAPPED_ROW_PIXELS if any(column.wraps for column in columns) else BODY_ROW_PIXELS


def _frozen_panes(sheet_id: int) -> dict[str, Any]:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": FROZEN_COLUMNS},
            },
            "fields": "gridProperties(frozenRowCount,frozenColumnCount)",
        }
    }


def _row_heights(sheet_id: int, first_row: int, end_row: int, pixels: int) -> dict[str, Any]:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": first_row, "endIndex": end_row},
            "properties": {"pixelSize": pixels},
            "fields": "pixelSize",
        }
    }


def _column_width(sheet_id: int, index: int, pixels: int, hidden: bool) -> dict[str, Any]:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            },
            "properties": {"pixelSize": pixels, "hiddenByUser": hidden},
            "fields": "pixelSize,hiddenByUser",
        }
    }


def _banding(sheet_id: int, row_count: int, column_count: int) -> dict[str, Any]:
    return {
        "addBanding": {
            "bandedRange": {
                "range": _range(sheet_id, 0, row_count, 0, column_count),
                "rowProperties": {
                    "headerColor": HEADER_BACKGROUND,
                    "firstBandColor": WHITE,
                    "secondBandColor": BAND_GREY,
                },
            }
        }
    }


def _verdict_colours(sheet_id: int, column: int, row_count: int) -> list[dict[str, Any]]:
    return [
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [_range(sheet_id, 1, row_count, column, column + 1)],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": verdict}]},
                        "format": {"backgroundColor": colour, "textFormat": {"bold": verdict == "apply"}},
                    },
                },
                "index": 0,
            }
        }
        for verdict, colour in VERDICT_COLOURS.items()
    ]


# a dropdown, but any other wording is still allowed
def _status_dropdown(sheet_id: int, column: int, row_count: int) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": _range(sheet_id, 1, row_count, column, column + 1),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": choice} for choice in STATUS_CHOICES],
                },
                "strict": False,
                "showCustomUi": True,
            },
        }
    }


# Each URL is shown as itself and made clickable. The link is a cell
# format, not a HYPERLINK formula, so nothing in an advert's address can run.
def _clickable_links(sheet_id: int, column: int, rows: list[list[str]]) -> dict[str, Any]:
    cells = []
    for row in rows[1:]:
        url = row[column] if column < len(row) else ""
        if url.startswith(("http://", "https://")):
            cells.append(
                {
                    "values": [
                        {
                            "userEnteredValue": {"stringValue": url},
                            "userEnteredFormat": {"textFormat": {"link": {"uri": url}}},
                        }
                    ]
                }
            )
        else:
            cells.append({"values": [{"userEnteredValue": {"stringValue": url}}]})
    return {
        "updateCells": {
            "rows": cells,
            "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": column},
            "fields": "userEnteredValue,userEnteredFormat.textFormat.link",
        }
    }
