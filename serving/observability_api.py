"""Authenticated GET-only dashboard routes; no consent/dispatch/provider path."""
import re
from anyio import to_thread
from fastapi import APIRouter, Depends, Request
from .observability_projection import LiveDataset
from observability.server import BadRequest, _query
from .api import Caller, require
from .service import ServiceError

router = APIRouter(prefix="/api/v1/observability")
KEY = re.compile(r"(requests|export)/(serving/(run_[0-9a-f]{32})/(trc_[0-9a-f]{32}))\Z")


@router.get("/{resource:path}")
async def observe(request: Request, resource: str, caller: Caller = Depends(require("runs:read"))):
    match = KEY.fullmatch(resource)
    if resource not in ("meta", "requests", "aggregates") and not match:
        raise ServiceError(404,"not_found","Not found.")
    try:
        q = _query(request.url.query)
    except BadRequest:
        raise ServiceError(400,"invalid_query","Invalid dashboard query.") from None
    selected = (match[3],match[4]) if match else None
    state = request.app.state
    dataset = await to_thread.run_sync(lambda: LiveDataset(state.service,caller.principal,state.observation_spans,selected=selected))
    viewer = dataset.viewer
    if resource == "meta":
        return dataset.meta(viewer)
    if resource == "requests":
        result = dataset.page(viewer,status=q.get("status"),route=q.get("route"),start=q.get("from"),end=q.get("to"),
                              page=int(q.get("page",1)),page_size=int(q.get("page_size",20)))
        return {**result, "sample": dataset.sample}
    if resource == "aggregates":
        return {"groups": dataset.aggregates(viewer,start=q.get("from"),end=q.get("to"),warmup_per_process=int(q.get("warmup",0))),
                "sample": dataset.sample}
    result = dataset.export(viewer,match[2]) if match[1] == "export" else dataset.get(viewer,match[2])
    if result is None:
        raise ServiceError(404,"not_found","Not found.")
    return result
