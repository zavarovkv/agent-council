"""Read-only GA4 property discovery and paginated reports."""

import re


class Analytics:
    def __init__(self, factory):
        self.factory = factory

    def read(self, args):
        if args["action"] == "properties":
            return (
                self.factory("analyticsadmin")
                .accountSummaries()
                .list(pageSize=200, pageToken=args.get("cursor"))
                .execute()
            )
        identity = args.get("property")
        if not isinstance(identity, str) or not re.fullmatch(
            r"properties/[1-9][0-9]{0,19}", identity
        ):
            raise ValueError("Choose an observed GA4 resource such as properties/123456.")
        api = self.factory("analytics").properties()
        if args["action"] == "metadata":
            return api.getMetadata(name=identity + "/metadata").execute()
        body = args.get("body")
        if not isinstance(body, dict) or not set(body) <= {
            "dateRanges",
            "dimensions",
            "metrics",
            "dimensionFilter",
            "metricFilter",
            "orderBys",
            "offset",
            "limit",
            "keepEmptyRows",
        }:
            raise ValueError("Unsupported GA4 report fields.")
        ranges = body.get("dateRanges")
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 4:
            raise ValueError("Provide one to four explicit GA4 date ranges.")
        for item in ranges:
            if not isinstance(item, dict) or not set(item) <= {"startDate", "endDate", "name"}:
                raise ValueError("Invalid GA4 date range.")
            for key in ("startDate", "endDate"):
                if not isinstance(item.get(key), str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}|[0-9]{1,4}daysAgo|today|yesterday", item[key]
                ):
                    raise ValueError("Provide explicit GA4 dates or relative days.")
        if not isinstance(body.get("metrics"), list) or not 1 <= len(body["metrics"]) <= 10:
            raise ValueError("Select one to ten GA4 metrics.")
        if not isinstance(body.get("dimensions", []), list) or len(body.get("dimensions", [])) > 9:
            raise ValueError("Use up to nine GA4 dimensions.")
        limit, offset = body.get("limit", 100), body.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise ValueError("Use an integer limit from 1 to 500 and a non-negative offset.")
        result = api.runReport(
            property=identity,
            body={**body, "limit": str(limit), "offset": str(offset), "returnPropertyQuota": True},
        ).execute()
        result["next_offset"] = (
            offset + len(result.get("rows", []))
            if offset + len(result.get("rows", [])) < result.get("rowCount", 0)
            else None
        )
        return result
