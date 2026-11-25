"""The sheet check must catch each defect the real sheet has had."""

import copy

import pytest

from job_hunt.job_pipeline.final_verdict import FinalVerdict
from job_hunt.sheet_sync.sheet_check import check_choices, check_tab
from job_hunt.sheet_sync.sheet_format import body_row_pixels, frozen_column_count
from job_hunt.sheet_sync.sheet_rows import LINK_COLUMN, jobs_columns

COLUMNS = jobs_columns(lambda company: "", lambda listing: None, lambda listing: FinalVerdict("apply"))


def good_cell(column, row_number: int) -> dict:
    cell = {
        "userEnteredFormat": {
            "horizontalAlignment": "CENTER" if column.centred else "LEFT",
            "wrapStrategy": "WRAP" if column.wraps else "CLIP",
        }
    }
    if column.choices or column.is_date:
        cell["dataValidation"] = {"condition": {"type": "ONE_OF_LIST"}}
    if column.header == LINK_COLUMN:
        cell.update(
            formattedValue=f"https://example.com/{row_number}", hyperlink=f"https://example.com/{row_number}"
        )
    return cell


def good_metadata() -> dict:
    header = [{"formattedValue": column.header} for column in COLUMNS]
    body = [{"values": [good_cell(column, row) for column in COLUMNS]} for row in range(2, 5)]
    return {
        "sheets": [
            {
                "properties": {
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": frozen_column_count(COLUMNS)}
                },
                "data": [
                    {
                        "columnMetadata": [
                            {"pixelSize": column.width, "hiddenByUser": column.header == "Listing key"}
                            for column in COLUMNS
                        ],
                        "rowMetadata": [{"pixelSize": 34}]
                        + [{"pixelSize": body_row_pixels(COLUMNS)}] * len(body),
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
    cell(metadata, 2, "Also on")["dataValidation"] = {"condition": {"type": "ONE_OF_LIST"}}


def drop_the_kind_dropdown(metadata):
    del cell(metadata, 1, "Kind")["dataValidation"]


def unlink_an_advert(metadata):
    del cell(metadata, 3, LINK_COLUMN)["hyperlink"]


def clip_the_bullets(metadata):
    cell(metadata, 1, "Why it's valid")["userEnteredFormat"]["wrapStrategy"] = "CLIP"


def swap_two_headers(metadata):
    header = metadata["sheets"][0]["data"][0]["rowData"][0]["values"]
    header[1], header[2] = header[2], header[1]


@pytest.mark.parametrize(
    "defect, expected",
    [
        (break_role_alignment, "'Role' row 2 is aligned CENTER"),
        (leave_dropdown_on_why, "'Also on' row 3 dropdown is True"),
        (drop_the_kind_dropdown, "'Kind' row 2 dropdown is False"),
        (unlink_an_advert, "advert link in row 4 isn't clickable"),
        (swap_two_headers, "headers are"),
        (clip_the_bullets, "'Why it's valid' row 2 wraps as CLIP"),
    ],
)
def test_each_defect_the_sheet_has_had_is_caught(defect, expected):
    metadata = copy.deepcopy(good_metadata())
    defect(metadata)
    problems = check_tab(FakeWorksheet(metadata), COLUMNS)
    assert any(expected in problem for problem in problems), problems


def test_a_value_outside_a_columns_dropdown_is_caught_but_the_users_status_is_free():
    header = [column.header for column in COLUMNS]
    row = [""] * len(COLUMNS)
    row[header.index("Kind")] = "graduate"
    row[header.index("Status")] = "Waiting to hear back"
    assert check_choices("Jobs", [header, row], COLUMNS) == []
    row[header.index("Kind")] = "Graduate scheme"
    assert check_choices("Jobs", [header, row], COLUMNS) == [
        "Jobs: 'Kind' has values outside its dropdown: ['Graduate scheme']"
    ]


def test_the_closing_soon_rule_reads_the_closes_column_against_today_and_skips_rows_dealt_with():
    from job_hunt.sheet_sync.sheet_format import closing_soon_formula

    headers = [column.header for column in COLUMNS]
    closes = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[headers.index("Closes")]
    status = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[headers.index("Status")]
    formula = closing_soon_formula(headers.index("Closes"), headers.index("Status"))
    assert formula.startswith(
        f'=AND(${closes}2<>"", DATEVALUE(${closes}2)>=TODAY(), DATEVALUE(${closes}2)<=TODAY()+7, NOT(OR('
    )
    assert f'${status}2="Applied"' in formula and f'${status}2="Duplicate"' in formula
    assert f'${status}2="To apply"' not in formula
    assert [column.header for column in COLUMNS if column.marks_closing_soon] == ["#", "Role", "Closes"]


def test_a_site_name_google_links_by_itself_is_not_a_defect():
    metadata = copy.deepcopy(good_metadata())
    cell(metadata, 1, "Found on").update(formattedValue="jobs.ac.uk", hyperlink="http://jobs.ac.uk")
    assert check_tab(FakeWorksheet(metadata), COLUMNS) == []
    cell(metadata, 1, "Found on").update(formattedValue="LinkedIn", hyperlink="https://evil.example.com")
    assert any(
        "'Found on' row 2 carries a link" in problem
        for problem in check_tab(FakeWorksheet(metadata), COLUMNS)
    )
