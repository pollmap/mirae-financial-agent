"""Internal execution contract for SQL/Graph/lexical/vector federation."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import QueryPlan, RetrievalTrace


@dataclass(slots=True)
class RetrievalPlan:
    """Candidate constraint and safe trace produced before SQL execution.

    ``candidate_uids=None`` means that SQL owns the whole eligible universe.
    An empty tuple is an intentional empty candidate universe. Ranked UIDs are
    used only for deterministic ordering of non-numeric search results; SQL
    still re-joins every UID to official catalog and metric rows.
    """

    execution_plan: QueryPlan
    candidate_uids: tuple[str, ...] | None = None
    ranked_candidate_uids: tuple[str, ...] = ()
    trace: list[RetrievalTrace] = field(default_factory=list)

    def intersect(self, candidates: list[str] | tuple[str, ...]) -> None:
        ordered = tuple(dict.fromkeys(str(uid) for uid in candidates))
        if self.candidate_uids is None:
            self.candidate_uids = ordered
            return
        allowed = set(ordered)
        self.candidate_uids = tuple(uid for uid in self.candidate_uids if uid in allowed)


def without_theme_filters(
    plan: QueryPlan,
    fields: set[str],
    *,
    clear_name_entities: bool = False,
) -> QueryPlan:
    """Clone a plan after a soft channel has replaced only named text filters."""

    payload = plan.model_dump(mode="json")
    groups = []
    for group in payload["filter_groups"]:
        conditions = [item for item in group["conditions"] if item["field"] not in fields]
        if conditions:
            groups.append({**group, "conditions": conditions})
    payload["filter_groups"] = groups
    if clear_name_entities:
        payload["entities"] = [item for item in payload["entities"] if item.get("code")]
        if plan.intent == "lookup" and not payload["entities"]:
            # The resolved candidate UID set now carries the lookup identity.
            # Reclassify only the internal execution copy so QueryPlan's
            # invariant (lookup always has an entity) remains intact; the
            # public/original plan and answer intent stay unchanged.
            payload["intent"] = "search"
    if plan.intent not in {"rank", "aggregate"}:
        metrics = list(payload["metrics"])
        for field in fields:
            if field not in metrics:
                metrics.append(field)
        payload["metrics"] = metrics[:12]
    payload["preserved_plan"] = None
    return QueryPlan.model_validate(payload)
