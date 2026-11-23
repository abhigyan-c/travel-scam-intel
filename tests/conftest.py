import asyncio


def pytest_asyncio_loop_factories(config, item):
    # Psycopg requires this on Windows; pytest-asyncio requires a factory on every platform.
    return {"selector": asyncio.SelectorEventLoop}
