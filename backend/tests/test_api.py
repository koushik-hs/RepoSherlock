import asyncio

from httpx import ASGITransport, AsyncClient

from reposherlock.main import app


def test_health() -> None:
    async def request_health() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "reposherlock"}

    asyncio.run(request_health())
