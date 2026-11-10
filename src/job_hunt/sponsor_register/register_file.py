import csv
import io
import re
from dataclasses import dataclass
from datetime import date

from job_hunt.common.http_client import HttpClient
from job_hunt.job_store.company_names import normalise_company_name, split_trading_name
from job_hunt.job_store.models import LicensedSponsor

# The GOV.UK content API (public, no key) describes the publication page,
# including the current register file; the file name changes every day.
PUBLICATION_API_URL = (
    "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
)
# "...Web_Register_-_2026-11-10.csv"
REGISTER_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})\.csv$")


class RegisterNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisterFile:
    url: str
    register_date: date


def find_current_register(http_client: HttpClient) -> RegisterFile:
    publication = http_client.get_json(PUBLICATION_API_URL)
    for attachment in publication.get("details", {}).get("attachments", []):
        url = attachment.get("url", "")
        dated_name = REGISTER_DATE_IN_NAME.search(url)
        if url.endswith(".csv") and dated_name:
            return RegisterFile(url, date.fromisoformat(dated_name.group(1)))
    raise RegisterNotFoundError("the GOV.UK publication lists no dated register CSV")


# Columns: Organisation Name, Town/City, County, Type & Rating, Route. Cells
# come padded with spaces, and the file starts with a byte-order mark.
def read_register(csv_text: str, register_date: date) -> list[LicensedSponsor]:
    sponsors = []
    for row in csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))):
        cells = {column.strip(): (value or "").strip() for column, value in row.items() if column}
        if not cells.get("Organisation Name"):
            continue
        legal_name, trading_name = split_trading_name(cells["Organisation Name"])
        sponsors.append(
            LicensedSponsor(
                organisation_name=cells["Organisation Name"],
                trading_name=trading_name,
                normalised_name=normalise_company_name(legal_name),
                normalised_trading_name=normalise_company_name(trading_name) if trading_name else None,
                town_city=cells.get("Town/City") or None,
                county=cells.get("County") or None,
                type_and_rating=cells.get("Type & Rating", ""),
                route=cells.get("Route", ""),
                register_date=register_date,
            )
        )
    return sponsors
