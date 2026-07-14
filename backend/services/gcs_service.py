"""
Google Cloud Storage helpers.
"""
import asyncio
import re

_BUCKET = "hirelyra-clients"
_REACHOUT_PREFIX = "client_reachouts/"
_GCS_PUBLIC_BASE = f"https://storage.googleapis.com/{_BUCKET}"


def _reachout_sort_key(url: str) -> int:
    """Extract the trailing number from filenames like reachout-3.png → 3."""
    match = re.search(r'-(\d+)\.\w+$', url)
    return int(match.group(1)) if match else 0


def _list_reachout_images_sync() -> list[str]:
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(_BUCKET)
    blobs = bucket.list_blobs(prefix=_REACHOUT_PREFIX)
    urls = [
        f"{_GCS_PUBLIC_BASE}/{b.name}"
        for b in blobs
        if not b.name.endswith("/")
    ]
    return sorted(urls, key=_reachout_sort_key)


async def list_reachout_images() -> list[str]:
    """Return public URLs of all images in the client_reachouts GCS folder."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _list_reachout_images_sync)
