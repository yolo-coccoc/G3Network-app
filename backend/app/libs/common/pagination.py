"""Page/page-size normalization shared by every paginated list operation.

Routers already validate ``page >= 1`` and ``1 <= page_size <= max``; a
service still normalizes because it is also called from tests, scripts and
other services. Keeping that rule in one place prevents the per-domain
copies from drifting apart.
"""

from dataclasses import dataclass

from app.libs.common.config import settings


@dataclass(frozen=True)
class PageWindow:
    """A normalized page request, ready for an ``OFFSET``/``LIMIT`` query.

    Attributes:
        page: Page number, at least ``settings.API_DEFAULT_PAGE`` (1).
        page_size: Rows per page, between 1 and ``settings.API_MAX_PAGE_SIZE``.
        offset: Rows to skip: ``(page - 1) * page_size``.
    """

    page: int
    page_size: int
    offset: int


def normalize_page_window(page: int, page_size: int) -> PageWindow:
    """Clamp a requested page into the allowed range and compute its offset.

    Args:
        page: Requested page number (1-based).
        page_size: Requested rows per page.

    Returns:
        The normalized page, page size and offset.
    """
    normalized_page = max(page, settings.API_DEFAULT_PAGE)
    normalized_page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    return PageWindow(
        page=normalized_page,
        page_size=normalized_page_size,
        offset=(normalized_page - 1) * normalized_page_size,
    )
