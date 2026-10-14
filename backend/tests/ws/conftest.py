import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _isolated_state(_clean_state: None) -> None:
    """Every test here starts from empty tables and an empty Redis."""
