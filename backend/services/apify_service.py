"""
Apify Cloud API client — start actor runs, poll completion, fetch dataset items.
"""
import asyncio
import os
import httpx

_BASE = "https://api.apify.com/v2"


def _token() -> str:
    t = os.environ.get("APIFY_API_TOKEN", "")
    if not t:
        raise RuntimeError("APIFY_API_TOKEN not set")
    return t


async def start_run(actor_id: str, run_input: dict) -> str:
    """Start an actor run. Returns run ID, or raises RuntimeError on failure."""
    url = f"{_BASE}/acts/{actor_id}/runs"
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(url, json=run_input, params={"token": _token()})
        resp.raise_for_status()
        body = resp.json()
    run_id = (body.get("data") or {}).get("id")
    if not run_id:
        raise RuntimeError(f"No run ID from Apify for actor {actor_id}: {body}")
    return run_id


async def poll_until_done(run_id: str, *, timeout: int = 360, interval: int = 10) -> str:
    """Poll until run SUCCEEDED. Returns defaultDatasetId, raises on failure/timeout."""
    url = f"{_BASE}/actor-runs/{run_id}"
    elapsed = 0
    async with httpx.AsyncClient(timeout=15) as http:
        while elapsed < timeout:
            resp = await http.get(url, params={"token": _token()})
            resp.raise_for_status()
            data = resp.json().get("data", {})
            status = data.get("status", "")
            if status == "SUCCEEDED":
                return data["defaultDatasetId"]
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run {run_id} ended: {status}")
            await asyncio.sleep(interval)
            elapsed += interval
    raise TimeoutError(f"Apify run {run_id} timed out after {timeout}s")


async def fetch_items(dataset_id: str, limit: int = 500) -> list[dict]:
    """Fetch all items from an Apify dataset."""
    url = f"{_BASE}/datasets/{dataset_id}/items"
    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.get(url, params={"token": _token(), "limit": limit, "clean": "true"})
        resp.raise_for_status()
        return resp.json()


async def run_actor(actor_id: str, run_input: dict, timeout: int = 360) -> list[dict]:
    """Convenience: start, poll, fetch. Returns item list."""
    run_id = await start_run(actor_id, run_input)
    dataset_id = await poll_until_done(run_id, timeout=timeout)
    return await fetch_items(dataset_id)
