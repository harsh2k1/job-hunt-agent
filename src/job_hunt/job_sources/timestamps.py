from datetime import datetime, timezone


# "2026-11-05T10:22:31Z", "2026-11-06T00:00:00+00:00", "2026-08-13 14:26:26 UTC"
# or a bare "2026-10-30",
# as APIs and schema.org markup write them. A value without a zone is taken
# as UTC; one that can't be read is unknown rather than an error.
def parse_iso_timestamp(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace(" UTC", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
