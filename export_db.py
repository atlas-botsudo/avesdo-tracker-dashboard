#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path


DB_PATH = Path("/Users/opentp/.openclaw/workspace-avesdo-tracker/memory/dev-tracker.db")
OUTPUT_PATH = Path("/Users/opentp/.openclaw/workspace-avesdo-tracker/dashboard/data.json")


def export_active_projects():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.name,
                b.name AS builder_name,
                p.city,
                p.state_province,
                p.country,
                p.status,
                p.expected_completion,
                p.price_min,
                p.price_max,
                p.total_units,
                p.source_url,
                p.website,
                p.project_type,
                p.address,
                p.raw_data
            FROM projects p
            JOIN builders b ON b.id = p.builder_id
            WHERE p.status != 'complete'
            ORDER BY p.name COLLATE NOCASE ASC
            """
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        item = dict(row)
        raw_data = item.get("raw_data")
        if raw_data:
            try:
                item["raw_data"] = json.loads(raw_data)
            except json.JSONDecodeError:
                item["raw_data"] = raw_data
        items.append(item)

    OUTPUT_PATH.write_text(json.dumps(items, indent=2))
    print(f"Exported {len(items)} active projects to {OUTPUT_PATH}")


if __name__ == "__main__":
    export_active_projects()
