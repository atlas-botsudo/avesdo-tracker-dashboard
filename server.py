#!/usr/bin/env python3
import json
import os
import secrets
import sqlite3
from collections import Counter
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, session, url_for


DEFAULT_DB_PATH = "/Users/opentp/.openclaw/workspace-avesdo-tracker/memory/dev-tracker.db"
DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB_PATH)
BASE_DIR = Path(__file__).resolve().parent
INDEX_PATH = BASE_DIR / "index.html"
DATA_JSON_PATH = BASE_DIR / "data.json"
PORT = int(os.environ.get("PORT", 8742))
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Offplan2026")

PROJECT_LIST_COLUMNS = [
    "id",
    "name",
    "builder_name",
    "city",
    "state_province",
    "country",
    "status",
    "expected_completion",
    "price_min",
    "price_max",
    "total_units",
    "source_url",
    "website",
    "project_type",
    "address",
]

PROJECT_DETAIL_COLUMNS = PROJECT_LIST_COLUMNS + [
    "source",
    "lat",
    "lng",
    "stories",
    "first_seen",
    "last_updated",
    "last_changed",
    "active",
    "raw_data",
    "builder_id",
    "builder_website",
    "builder_hq_city",
    "builder_hq_state_province",
    "builder_hq_country",
    "builder_public_ticker",
]

SORT_MAP = {
    "name": lambda item: safe_text(item.get("name")),
    "builder": lambda item: safe_text(item.get("builder_name")),
    "city": lambda item: safe_text(item.get("city")),
    "state_province": lambda item: safe_text(item.get("state_province")),
    "country": lambda item: safe_text(item.get("country")),
    "status": lambda item: safe_text(item.get("status")),
    "move_in_year": lambda item: (extract_move_in_year(item.get("status"), item.get("expected_completion")) or 9999, safe_text(item.get("name"))),
    "price_min": lambda item: (item.get("price_min") if item.get("price_min") is not None else 999999999, safe_text(item.get("name"))),
    "price_max": lambda item: (item.get("price_max") if item.get("price_max") is not None else 999999999, safe_text(item.get("name"))),
    "total_units": lambda item: (item.get("total_units") if item.get("total_units") is not None else 999999999, safe_text(item.get("name"))),
}


def safe_text(value):
    return str(value or "").casefold()


def parse_json_maybe(value):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def extract_move_in_year(status, expected_completion):
    if expected_completion:
        for token in str(expected_completion).replace("/", "-").split("-"):
            if len(token) == 4 and token.isdigit():
                return int(token)
    if status and status.startswith("move-in-"):
        suffix = status.replace("move-in-", "", 1)
        if suffix.isdigit():
            return int(suffix)
    return None


def format_price_range(price_min, price_max):
    if price_min is None and price_max is None:
        return None
    if price_min is not None and price_max is not None:
        if price_min == price_max:
            return f"${price_min:,.0f}"
        return f"${price_min:,.0f} - ${price_max:,.0f}"
    if price_min is not None:
        return f"From ${price_min:,.0f}"
    return f"Up to ${price_max:,.0f}"


def clamp_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def normalize_project(record):
    data = dict(record)
    data["move_in_year"] = extract_move_in_year(data.get("status"), data.get("expected_completion"))
    data["price_range"] = format_price_range(data.get("price_min"), data.get("price_max"))
    return data


def normalize_project_detail(record):
    data = normalize_project(record)
    data["raw_data"] = parse_json_maybe(data.get("raw_data"))
    return data


def sqlite_rows_to_projects(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.name,
                p.builder_id,
                b.name AS builder_name,
                p.city,
                p.state_province,
                p.country,
                p.address,
                p.lat,
                p.lng,
                p.project_type,
                p.total_units,
                p.stories,
                p.status,
                p.price_min,
                p.price_max,
                p.expected_completion,
                p.website,
                p.source_url,
                p.source,
                p.first_seen,
                p.last_updated,
                p.last_changed,
                p.raw_data,
                p.active,
                b.website AS builder_website,
                b.hq_city AS builder_hq_city,
                b.hq_state_province AS builder_hq_state_province,
                b.hq_country AS builder_hq_country,
                b.public_ticker AS builder_public_ticker
            FROM projects p
            JOIN builders b ON b.id = p.builder_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def json_rows_to_projects(path):
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = payload

    projects = []
    for item in items:
        project = {column: item.get(column) for column in PROJECT_DETAIL_COLUMNS}
        project["raw_data"] = item.get("raw_data")
        projects.append(project)
    return projects


def load_projects():
    data_source = "json"
    env_db_path = os.environ.get("DB_PATH")
    if env_db_path and Path(DB_PATH).exists():
        data_source = "sqlite"
        projects = sqlite_rows_to_projects(DB_PATH)
    elif not env_db_path and Path(DEFAULT_DB_PATH).exists():
        data_source = "sqlite"
        projects = sqlite_rows_to_projects(DEFAULT_DB_PATH)
    else:
        projects = json_rows_to_projects(DATA_JSON_PATH)
    return projects, data_source


def build_indexes(projects):
    normalized = [normalize_project_detail(project) for project in projects]
    by_id = {str(project["id"]): project for project in normalized if project.get("id") is not None}
    builder_counts = Counter()
    builders = {}

    for project in normalized:
        builder_name = project.get("builder_name") or "Unknown"
        builder_key = safe_text(builder_name)
        builder_counts[builder_key] += 1
        builder_entry = builders.setdefault(
            builder_key,
            {
                "id": project.get("builder_id"),
                "name": builder_name,
                "website": project.get("builder_website"),
                "hq_city": project.get("builder_hq_city"),
                "hq_state_province": project.get("builder_hq_state_province"),
                "hq_country": project.get("builder_hq_country"),
                "project_count": 0,
            },
        )
        if not builder_entry.get("website") and project.get("builder_website"):
            builder_entry["website"] = project.get("builder_website")
        if not builder_entry.get("hq_city") and project.get("builder_hq_city"):
            builder_entry["hq_city"] = project.get("builder_hq_city")
        if not builder_entry.get("hq_state_province") and project.get("builder_hq_state_province"):
            builder_entry["hq_state_province"] = project.get("builder_hq_state_province")
        if not builder_entry.get("hq_country") and project.get("builder_hq_country"):
            builder_entry["hq_country"] = project.get("builder_hq_country")

    builder_items = []
    for builder_key, builder in builders.items():
        builder["project_count"] = builder_counts[builder_key]
        builder_items.append(builder)

    builder_items.sort(key=lambda item: (-item["project_count"], safe_text(item.get("name"))))
    return normalized, by_id, builder_items


ALL_PROJECTS, DATA_SOURCE = load_projects()
PROJECTS, PROJECTS_BY_ID, BUILDERS = build_indexes(ALL_PROJECTS)


def build_project_filters(args):
    status = (args.get("status") or "").strip().lower()
    country = (args.get("country") or "").strip().upper()
    city = (args.get("city") or "").strip()
    search = (args.get("search") or "").strip()

    def matches(project):
        project_status = (project.get("status") or "").lower()
        if status:
            if status == "all-active":
                if project_status == "complete":
                    return False
            elif project_status != status:
                return False
        elif project_status == "complete":
            return False

        if country and (project.get("country") or "").upper() != country:
            return False

        if city and city.casefold() not in safe_text(project.get("city")):
            return False

        if search:
            haystacks = [project.get("name"), project.get("builder_name")]
            if not any(search.casefold() in safe_text(value) for value in haystacks):
                return False

        return True

    return matches


def fetch_projects(args):
    limit = clamp_int(args.get("limit"), 50, 1, 100)
    offset = clamp_int(args.get("offset"), 0, 0, 100000)
    sort_by = args.get("sort_by", "name")
    sort_dir = "desc" if args.get("sort_dir", "asc").lower() == "desc" else "asc"
    matcher = build_project_filters(args)
    items = [project for project in PROJECTS if matcher(project)]
    sort_key = SORT_MAP.get(sort_by, SORT_MAP["name"])
    items.sort(key=sort_key, reverse=sort_dir == "desc")
    total = len(items)
    page_items = items[offset : offset + limit]

    return {
        "items": [{key: item.get(key) for key in PROJECT_LIST_COLUMNS + ["move_in_year", "price_range"]} for item in page_items],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + limit < total,
        },
        "sort": {
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
        "data_source": DATA_SOURCE,
    }


def fetch_project_detail(project_id):
    return PROJECTS_BY_ID.get(str(project_id))


def fetch_stats():
    active_projects = [project for project in PROJECTS if project.get("status") != "complete"]
    status_counts = Counter(project.get("status") or "unknown" for project in PROJECTS)
    country_counts = Counter((project.get("country") or "Unknown") for project in active_projects)
    city_counts = Counter(
        (project.get("city"), project.get("state_province"), project.get("country"))
        for project in active_projects
    )

    top_cities = []
    for (city, state_province, country), count in city_counts.most_common(12):
        top_cities.append(
            {
                "city": city,
                "state_province": state_province,
                "country": country,
                "count": count,
            }
        )

    by_status = [
        {"status": status, "count": count}
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    by_country = [
        {"country": country, "count": count}
        for country, count in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "summary": {
            "total_active": len(active_projects),
            "preconstruction": status_counts.get("preconstruction", 0),
            "under_construction": status_counts.get("under-construction", 0),
            "move_in_now": status_counts.get("move-in-now", 0),
            "complete": status_counts.get("complete", 0),
        },
        "by_status": by_status,
        "by_country": by_country,
        "top_cities": top_cities,
        "data_source": DATA_SOURCE,
    }


def fetch_builders(args):
    limit = clamp_int(args.get("limit"), 20, 1, 100)
    search = (args.get("search") or "").strip().casefold()
    items = BUILDERS
    if search:
        items = [builder for builder in BUILDERS if search in safe_text(builder.get("name"))]
    return {"items": items[:limit], "data_source": DATA_SOURCE}


def read_index_html():
    return INDEX_PATH.read_bytes()


def login_page(error_message=None, next_path="/"):
    message_html = ""
    if error_message:
        message_html = f'<p style="margin:0 0 16px;color:#ff6b6b;font-size:14px;">{error_message}</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Avesdo Dev Tracker Login</title>
  <style>
    :root {{
      --bg: #0a0f15;
      --panel: #111925;
      --text: #e8eef7;
      --muted: #95a3b8;
      --line: rgba(255,255,255,0.08);
      --accent: #44baf4;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at top left, rgba(68, 186, 244, 0.18), transparent 32%),
        linear-gradient(180deg, #081018 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "SF Pro Display", "Segoe UI", sans-serif;
    }}
    .card {{
      width: min(420px, 100%);
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
    }}
    h1 {{ margin: 0 0 10px; font-size: 28px; }}
    p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.5; }}
    input {{
      width: 100%;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      color: var(--text);
      margin-bottom: 14px;
    }}
    button {{
      width: 100%;
      padding: 12px 14px;
      border: 0;
      border-radius: 12px;
      cursor: pointer;
      color: white;
      font: inherit;
      background: linear-gradient(135deg, #287bff, var(--accent));
    }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Dashboard Login</h1>
    <p>Enter the dashboard password to continue.</p>
    {message_html}
    <input type="hidden" name="next" value="{next_path}">
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" required>
    <button type="submit">Sign In</button>
  </form>
</body>
</html>"""


def is_authenticated():
    return session.get("authenticated") is True


app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.before_request
def require_login():
    if request.method == "OPTIONS":
        return ("", 204)
    if request.path in {"/login"}:
        return None
    if request.path == "/logout" and request.method == "GET":
        return None
    if is_authenticated():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    next_path = request.args.get("next") or request.form.get("next") or "/"
    if not next_path.startswith("/"):
        next_path = "/"
    if request.method == "GET":
        if is_authenticated():
            return redirect(url_for("root"))
        return Response(login_page(next_path=next_path), mimetype="text/html")

    submitted_password = request.form.get("password", "")
    if submitted_password == PASSWORD:
        session.clear()
        session["authenticated"] = True
        return redirect(next_path)
    return Response(login_page("Incorrect password", next_path=next_path), mimetype="text/html")


@app.route("/logout", methods=["GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
def root():
    return Response(read_index_html(), mimetype="text/html")


@app.route("/api/projects", methods=["GET"])
def api_projects():
    return jsonify(fetch_projects(request.args))


@app.route("/api/projects/<project_id>", methods=["GET"])
def api_project_detail(project_id):
    payload = fetch_project_detail(project_id)
    if payload is None:
        return jsonify({"error": "Project not found"}), 404
    return jsonify(payload)


@app.route("/api/stats", methods=["GET"])
def api_stats():
    return jsonify(fetch_stats())


@app.route("/api/builders", methods=["GET"])
def api_builders():
    return jsonify(fetch_builders(request.args))


@app.route("/<path:path>", methods=["GET", "OPTIONS"])
def catch_all(path):
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify({"error": "Not found"}), 404


def run_server():
    print(f"Serving dashboard on http://127.0.0.1:{PORT} using {DATA_SOURCE} data")
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    run_server()
