import httpx


async def call_scraper(url: str, client_data: dict) -> dict:
    async with httpx.AsyncClient(timeout=25) as http:
        resp = await http.post(url, json={"client": client_data})
        resp.raise_for_status()
    return resp.json()
