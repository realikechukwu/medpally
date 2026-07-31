from datetime import date

from engine.featured import FeaturedCandidate, rank_featured, score_candidate, week_start_for


def candidate(**kwargs):
    defaults = {
        "paper_id": 1,
        "study_type": "RCT",
        "journal_prestige": 2,
        "likes": 0,
        "saves": 0,
        "feed_date": date(2026, 7, 26),
    }
    defaults.update(kwargs)
    return FeaturedCandidate(**defaults)


def test_ranking_is_transparent_capped_and_deterministic():
    rct = candidate(paper_id=1)
    meta_general = candidate(paper_id=2, study_type="Meta-analysis", journal_prestige=4)
    engaged = candidate(paper_id=3, likes=99, saves=99)

    assert score_candidate(engaged).components["engagement"] == 10
    assert [item.candidate.paper_id for item in rank_featured([rct, meta_general])] == [2, 1]


def test_ranking_floors_thin_weeks_and_breaks_ties_by_recency_then_id():
    low = candidate(study_type="Other", journal_prestige=2)
    older = candidate(paper_id=1, feed_date=date(2026, 7, 20))
    newer = candidate(paper_id=2, feed_date=date(2026, 7, 21))
    same_day_higher_id = candidate(paper_id=3, feed_date=date(2026, 7, 21))

    assert rank_featured([low]) == []
    assert [
        item.candidate.paper_id for item in rank_featured([older, newer, same_day_higher_id])
    ] == [3, 2, 1]


def test_week_start_is_monday():
    assert week_start_for(date(2026, 7, 27)) == date(2026, 7, 27)
    assert week_start_for(date(2026, 8, 2)) == date(2026, 7, 27)
