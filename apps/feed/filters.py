"""Validated, URL-backed feed filters."""

from __future__ import annotations

from dataclasses import dataclass

DESIGN_STUDY_TYPES = {
    "rct": ("RCT",),
    "meta-analysis": ("Meta-analysis",),
    "systematic-review": ("Systematic review",),
    "guideline": ("Guideline",),
    "cohort": ("Prospective cohort", "Retrospective cohort"),
}


@dataclass(frozen=True, slots=True)
class FeedFilters:
    tab: str = "all"
    design: str = ""
    sort: str = "feed"
    cursor: str = ""

    @classmethod
    def from_request(cls, request) -> FeedFilters:
        tab = request.GET.get("tab", "all")
        # Keep old bookmarked links working while the new tab URLs roll out.
        if request.GET.get("unseen") == "1":
            tab = "unseen"
        design = request.GET.get("design", "")
        sort = request.GET.get("sort", "feed")
        return cls(
            tab=tab if tab in {"all", "unseen", "featured"} else "all",
            design=design if design in DESIGN_STUDY_TYPES else "",
            sort=sort if sort in {"feed", "pub"} else "feed",
            cursor=request.GET.get("cursor", ""),
        )

    def is_default(self) -> bool:
        return self.tab == "all" and not self.design and self.sort == "feed"

    def study_types(self) -> tuple[str, ...]:
        return DESIGN_STUDY_TYPES.get(self.design, ())
