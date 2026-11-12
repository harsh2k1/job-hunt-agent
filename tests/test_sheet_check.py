"""The sheet check must catch each defect the real sheet has had."""

import copy

import pytest

from job_hunt.sheet_sync.sheet_check import check_tab
from job_hunt.sheet_sync.sheet_format import FROZEN_COLUMNS
from job_hunt.sheet_sync.sheet_rows import LINK_COLUMN, jobs_columns

COLUMNS = jobs_columns(lambda company: "", lambda listing: None)


def good_cell(column, row_number: int) -> dict:
    cell = {"userEnteredFormat": {"horizontalAlignment": "CENTER" if column.centred else "LEFT"}}
    if column.header == "Status":
        cell["dataValidation"] = {"condition": {"type": "ONE_OF_LIST"}}
    if column.header == LINK_COLUMN:
        cell.update(formattedValue="Open advert ↗", hyperlink=f"https://example.com/{row_number}")
    return cell


def good_metadata() -> dict:
    header = [{"formattedValue": column.header} for column in COLUMNS]
    body = [{"values": [good_cell(column, row) for column in COLUMNS]} for row in range(2, 5)]
    return {
        "sheets": [
            {
                "properties": {"gridProperties": {"frozenRowCount": 1, "frozenColumnCount": FROZEN_COLUMNS}},
                "data": [
                    {
                        "columnMetadata": [
                            {"pixelSize": column.width, "hiddenByUser": column.header == "Listing key"}
                            for column in COLUMNS
                        ],
                        "rowData": [{"values": header}, *body],
                    }
                ],
            }
        ]
    }


class FakeWorksheet:
    title = "Jobs"

    def __init__(self, metadata: dict):
        self.spreadsheet = self
        self._metadata = metadata

    def fetch_sheet_metadata(self, params):
        return self._metadata


def cell(metadata: dict, row: int, header: str) -> dict:
    index = [column.header for column in COLUMNS].index(header)
    return metadata["sheets"][0]["data"][0]["rowData"][row]["values"][index]


def test_a_sheet_laid_out_as_defined_passes():
    assert check_tab(FakeWorksheet(good_metadata()), COLUMNS) == []


def break_role_alignment(metadata):
    cell(metadata, 1, "Role")["userEnteredFormat"]["horizontalAlignment"] = "CENTER"


def leave_dropdown_on_why(metadata):
    cell(metadata, 2, "Why")["dataValidation"] = {"condition": {"type": "ONE_OF_LIST"}}


def unlink_an_advert(metadata):
    del cell(metadata, 3, LINK_COLUMN)["hyperlink"]


def swap_two_headers(metadata):
    header = metadata["sheets"][0]["data"][0]["rowData"][0]["values"]
    header[1], header[2] = header[2], header[1]


@pytest.mark.parametrize(
    "defect, expected",
    [
        (break_role_alignment, "'Role' row 2 is aligned CENTER"),
        (leave_dropdown_on_why, "'Why' row 3 has a dropdown rule"),
        (unlink_an_advert, "advert link in row 4 isn't clickable"),
        (swap_two_headers, "headers are"),
    ],
)
def test_each_defect_the_sheet_has_had_is_caught(defect, expected):
    metadata = copy.deepcopy(good_metadata())
    defect(metadata)
    problems = check_tab(FakeWorksheet(metadata), COLUMNS)
    assert any(expected in problem for problem in problems), problems
