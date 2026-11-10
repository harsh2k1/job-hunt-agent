from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    # when the page last changed, as the site reports it; None if it doesn't
    last_modified: str | None


# A sitemap (sitemaps.org) is the list of pages a site publishes for
# crawlers. Tags are matched by local name, so any namespace works.
def read_sitemap(sitemap_xml: str) -> list[SitemapEntry]:
    entries = []
    for element in ElementTree.fromstring(sitemap_xml).iter():
        if _local_name(element.tag) != "url":
            continue
        children = {_local_name(child.tag): (child.text or "").strip() for child in element}
        if children.get("loc"):
            entries.append(SitemapEntry(children["loc"], children.get("lastmod") or None))
    return entries


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
