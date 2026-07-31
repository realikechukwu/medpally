"""Pure, transparent scoring for weekly featured papers."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

DESIGN_WEIGHTS = {
    "RCT": 10, "Meta-analysis": 9, "Systematic review": 8, "Guideline": 7,
    "Prospective cohort": 5, "Retrospective cohort": 4, "Case-control": 3,
    "Case series": 1, "Narrative review": 1, "Other": 0,
}
MIN_SCORE = 10


@dataclass(frozen=True, slots=True)
class FeaturedCandidate:
    paper_id: int
    study_type: str
    journal_prestige: int
    likes: int
    saves: int
    feed_date: date


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    candidate: FeaturedCandidate
    score: float
    components: Mapping[str, float]


def score_candidate(candidate: FeaturedCandidate) -> ScoredCandidate:
    design = float(DESIGN_WEIGHTS.get(candidate.study_type, 0) * 3)
    prestige = float(candidate.journal_prestige * 2)
    engagement = float(min(candidate.likes * 2 + candidate.saves, 10))
    return ScoredCandidate(candidate, design + prestige + engagement, {"design": design, "prestige": prestige, "engagement": engagement})


def rank_featured(candidates: list[FeaturedCandidate], *, limit: int = 10, min_score: float = MIN_SCORE) -> list[ScoredCandidate]:
    scored = [score_candidate(candidate) for candidate in candidates]
    return sorted(
        (item for item in scored if item.score >= min_score),
        key=lambda item: (-item.score, -item.candidate.feed_date.toordinal(), -item.candidate.paper_id),
    )[:limit]


def week_start_for(day: date) -> date:
    return day - timedelta(days=day.weekday())
