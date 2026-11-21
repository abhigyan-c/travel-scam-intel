"""Non-writing public MCP smoke check; does not queue research or submit reports."""

import argparse
import asyncio
import json

from mcp import Client


async def check(url):
    async with Client(url, mode="legacy", read_timeout_seconds=15) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        expected = {
            "resolve_scam_location",
            "search_scam_patterns",
            "get_scam_pattern",
            "get_scam_evidence",
            "match_possible_scam",
            "get_recent_patterns",
            "submit_scam_report",
            "request_scam_report",
            "get_scam_report",
            "wait_for_scam_report",
            "watch_scam_report",
        }
        if names != expected:
            raise ValueError("unexpected_public_tool_list")
        from app.explorer import EXPLORER_URI

        resource = await client.read_resource(EXPLORER_URI)
        if not resource.contents or resource.contents[0].mime_type != "text/html;profile=mcp-app":
            raise ValueError("explorer_resource_failed")
        location = await client.call_tool("resolve_scam_location", {"location": "Bangkok"})
        if location.is_error or not location.structured_content.get("location_id"):
            raise ValueError("seed_location_resolution_failed")
        identity = location.structured_content["location_id"]
        results = {}
        for name, arguments in [
            ("search_scam_patterns", {"location_id": identity, "request_research": False}),
            ("get_recent_patterns", {"location_id": identity, "request_research": False}),
            (
                "match_possible_scam",
                {
                    "location_id": identity,
                    "incident": "Synthetic deployment verification without a real incident.",
                },
            ),
        ]:
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise ValueError("public_read_failed")
            results[name] = {
                key: result.structured_content[key]
                for key in ("outcome", "status")
                if key in result.structured_content
            }
        for name in ("get_scam_pattern", "get_scam_evidence"):
            result = await client.call_tool(
                name, {"pattern_id": "00000000-0000-4000-8000-000000000001"}
            )
            if not result.is_error or result.structured_content["error"]["code"] != "not_found":
                raise ValueError("missing_pattern_error_failed")
        rejected = await client.call_tool(
            "submit_scam_report",
            {
                "report_text": "Synthetic transport check which must not be persisted.",
                "provenance_class": "SECOND_HAND",
                "idempotency_key": "mcp-smoke-no-submission",
                "confirmed_by_user": False,
            },
        )
        if not rejected.is_error:
            raise ValueError("unconfirmed_report_not_rejected")
        for name, arguments in [
            ("get_scam_report", {"run_id": "00000000-0000-4000-8000-000000000001"}),
            ("watch_scam_report", {"run_id": "00000000-0000-4000-8000-000000000001"}),
            (
                "wait_for_scam_report",
                {
                    "run_id": "00000000-0000-4000-8000-000000000001",
                    "watch_id": "00000000-0000-4000-8000-000000000002",
                    "after_revision": "0" * 64,
                    "wait_seconds": 0,
                },
            ),
        ]:
            result = await client.call_tool(name, arguments)
            if not result.is_error or result.structured_content["error"]["code"] != "not_found":
                raise ValueError("missing_report_error_failed")
        return {
            "tool_count": len(names),
            "reads": results,
            "missing_patterns": "not_found",
            "missing_reports": "not_found",
            "unconfirmed_report": "rejected",
            "research_requested": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.url)), indent=2))


if __name__ == "__main__":
    main()
