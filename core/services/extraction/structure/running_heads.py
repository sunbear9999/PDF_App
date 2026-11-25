"""Local repetition evidence: a chapter need not occupy most of a book."""

WINDOW_PAGES = 12
MIN_PAGES = 3
MIN_EDGE_SHARE = 0.8


def locally_repeated(pages, edge_pages):
    """Require three edge occurrences within a short run of physical pages.

    Global repetition remains useful for stitched web prints; this additional
    test requires actual edge placement, not merely a short standalone block.
    """
    ordered = sorted(p for p in pages if p is not None)
    for index in range(len(ordered) - MIN_PAGES + 1):
        window = ordered[index:index + MIN_PAGES]
        if (window[-1] - window[0] < WINDOW_PAGES
                and sum(p in edge_pages for p in window) / len(window) >= MIN_EDGE_SHARE):
            return True
    return False
