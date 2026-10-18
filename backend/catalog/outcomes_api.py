"""Outcome endpoints: reading lists by outcome, and outcome coverage for a build.

    POST /api/recommend  {outcome_set_id | outcome_ids, focus?, tiers?, levels?, per_outcome?, exclude_ids?}
    POST /api/coverage   {state, outcome_set_ids?}

The matching itself lives in catalog.outcomes.
"""
from ninja import Router, Schema
from ninja.errors import HttpError

from catalog import outcomes
from catalog.api import _pack

router = Router(tags=["outcomes"])

MAX_PER_OUTCOME = 25


class RecommendIn(Schema):
    outcome_set_id: str | None = None
    outcome_ids: list[str] = []
    focus: str = ""
    tiers: list[str] = []
    levels: list[str] = []
    per_outcome: int = 8
    exclude_ids: list[str] = []


class CoverageIn(Schema):
    state: dict
    # Omitted: use the build's own state["outcome_set_ids"].
    outcome_set_ids: list[str] | None = None


@router.post("/recommend")
def recommend(request, body: RecommendIn, pack: str | None = None):
    p = _pack(pack)
    outcome_set, unknown = None, []
    if body.outcome_set_id:
        sets, specs, missing = outcomes.specs_for_sets(p, [body.outcome_set_id])
        if missing:
            raise HttpError(404, f"No outcome set with id {body.outcome_set_id!r}.")
        outcome_set = sets[0]
        if body.outcome_ids:
            wanted = set(body.outcome_ids)
            unknown = [i for i in body.outcome_ids if i not in {s.id for s in specs}]
            specs = [s for s in specs if s.id in wanted]
    elif body.outcome_ids:
        specs, unknown = outcomes.specs_for_ids(p, body.outcome_ids)
        if not specs:
            raise HttpError(404, "None of those outcomes exist.")
    else:
        raise HttpError(400, "Send an outcome_set_id or a list of outcome_ids.")
    per = min(max(body.per_outcome, 1), MAX_PER_OUTCOME)
    return {
        "outcome_set": outcome_set,
        "unknown_ids": unknown,
        "outcomes": outcomes.recommend(p, specs, focus=body.focus, tiers=body.tiers, levels=body.levels,
                                       per_outcome=per, exclude_ids=body.exclude_ids),
    }


@router.post("/coverage")
def coverage(request, body: CoverageIn, pack: str | None = None):
    state = body.state or {}
    ids = body.outcome_set_ids if body.outcome_set_ids is not None else state.get("outcome_set_ids") or []
    if not isinstance(ids, list):
        raise HttpError(400, "outcome_set_ids must be a list.")
    return outcomes.coverage(_pack(pack), state, [str(i) for i in ids])
