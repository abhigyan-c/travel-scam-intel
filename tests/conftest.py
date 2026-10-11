import asyncio
import sys


def pytest_asyncio_loop_factories(config, item):
    # Psycopg async connections require a selector loop on Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None
