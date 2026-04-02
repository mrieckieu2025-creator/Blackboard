"""
BB Assistant — Flask API Server
Handles auth, Blackboard sync, AI drafting, syllabus parsing.
Runs background jobs via APScheduler.
"""
import os, json, secrets, hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from apscheduler.schedulers.background import BackgroundScheduler
import database as db
from blackboard_client import BlackboardClient
from ai_helper import draft_assignment, parse_syllabus, suggest_study_plan
from syllabus_parser import extract_text

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

BB_URL = "https://blackboard.ie.edu"


# ── CORS (allow extension origin) ────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return jsonify({}), 200


# ── Auth helpers ──────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def make_token() -> str:
    return secrets.token_hex(32)

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "").strip()
        if not token:
            return jsonify({"error": "Missing token"}), 401
        user = db.get_user_by_token(token)
        if not user:
            return jsonify({"error": "Invalid or expired token"}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not username or not email or not password:
        return jsonify({"error": "username, email and password are required"}), 400
    if db.get_user_by_username(username):
        return jsonify({"error": "Username already taken"}), 409
    user_id = db.create_user(username, email, hash_password(password))
    token = make_token()
    db.save_token(user_id, token)
    return jsonify({"token": token, "user_id": user_id, "username": username}), 201

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = db.get_user_by_username(username)
    if not user or user["password_hash"] != hash_password(password):
        return jsonify({"error": "Invalid credentials"}), 401
    token = make_token()
    db.save_token(user["id"], token)
    return jsonify({"token": token, "user_id": user["id"], "username": user["username"]})

@app.route("/api/me", methods=["GET"])
@token_required
def me():
    u = g.user
    return jsonify({
        "user_id": u["id"], "username": u["username"], "email": u["email"],
        "bb_configured": bool(u["bb_username"])
    })


# ── Blackboard credentials ────────────────────────────────────────────────────
@app.route("/api/bb/configure", methods=["POST"])
@token_required
def configure_bb():
    data = request.json or {}
    bb_username = data.get("bb_username", "").strip()
    bb_password = data.get("bb_password", "")
    if not bb_username or not bb_password:
        return jsonify({"error": "bb_username and bb_password required"}), 400
    client = BlackboardClient(BB_URL, bb_username, bb_password)
    if not client.login():
        return jsonify({"error": "Could not log in to Blackboard — check your credentials"}), 401
    db.update_user_blackboard(g.user["id"], BB_URL, bb_username, bb_password)
    return jsonify({"message": "Blackboard connected successfully"})


@app.route("/api/bb/session", methods=["POST"])
@token_required
def bb_session():
    """Accept session cookies grabbed from the user's browser (handles SSO/2FA)."""
    data = request.json or {}
    cookie_string = data.get("cookies", "").strip()
    if not cookie_string:
        return jsonify({"error": "No cookies provided"}), 400
    # Verify the cookies actually work
    client = BlackboardClient(BB_URL, cookie_string=cookie_string)
    if not client.login():
        return jsonify({"error": "Blackboard session invalid or expired — please log into Blackboard in your browser first, then try again"}), 401
    # Store cookie string as the "password" field (reused for simplicity)
    db.update_user_blackboard(g.user["id"], BB_URL, "sso_user", cookie_string)
    return jsonify({"message": "Blackboard session connected successfully"})


# ── Courses ───────────────────────────────────────────────────────────────────
@app.route("/api/courses", methods=["GET"])
@token_required
def get_courses():
    courses = db.get_courses(g.user["id"])
    result = []
    for c in courses:
        stats = db.get_course_stats(c["id"], g.user["id"])
        result.append({**dict(c), **stats})
    return jsonify(result)

@app.route("/api/courses", methods=["POST"])
@token_required
def add_course():
    data = request.json or {}
    course_id = db.add_course_manual(
        g.user["id"],
        data.get("name", "New Course"),
        data.get("code", ""),
        data.get("instructor", ""),
        data.get("term", "")
    )
    return jsonify({"id": course_id}), 201


# ── Sync with Blackboard ──────────────────────────────────────────────────────
@app.route("/api/sync", methods=["POST"])
@token_required
def sync():
    user = db.get_user_by_id(g.user["id"])
    if not user["bb_username"]:
        return jsonify({"error": "Blackboard not configured"}), 400
    result = _do_sync(user)
    return jsonify(result)

def _do_sync(user):
    """Core sync logic — called by API and by scheduler."""
    # If bb_username is 'sso_user', the password field holds the cookie string
    if user.get("bb_username") == "sso_user":
        client = BlackboardClient(BB_URL, cookie_string=user["bb_password_enc"])
    else:
        client = BlackboardClient(BB_URL, user["bb_username"], user["bb_password_enc"])
    if not client.login():
        return {"error": "Blackboard session expired — please open Blackboard in Chrome and click Sync again"}

    synced_courses = 0
    synced_assignments = 0
    new_syllabi = 0

    courses = client.get_courses()
    for course_data in courses:
        course_id = db.upsert_course(
            user["id"],
            course_data["bb_course_id"],
            course_data["name"],
            course_data["code"],
            course_data.get("instructor", ""),
            course_data.get("term", "")
        )
        synced_courses += 1

        assignments = client.get_assignments(course_data["bb_course_id"])
        for a in assignments:
            db.upsert_assignment(
                user["id"], course_id,
                a["bb_assignment_id"], a["title"],
                a.get("description", ""), a.get("instructions", ""),
                a.get("due_date"), a.get("points_possible"),
                a.get("assignment_type", "assignment")
            )
            synced_assignments += 1

        # Scan for syllabus documents
        syllabus_docs = client.get_syllabus_documents(course_data["bb_course_id"])
        for doc in syllabus_docs:
            filepath = client.download_document(doc["url"], UPLOAD_DIR, doc["filename"])
            if filepath:
                raw_text = extract_text(filepath)
                parsed = parse_syllabus(raw_text)
                db.save_syllabus(user["id"], course_id, doc["filename"], raw_text, json.dumps(parsed))
                _import_syllabus_items(user["id"], course_id, parsed)
                new_syllabi += 1

    db.update_last_sync(user["id"])
    return {
        "synced_courses": synced_courses,
        "synced_assignments": synced_assignments,
        "new_syllabi": new_syllabi,
        "synced_at": datetime.now().isoformat()
    }

def _import_syllabus_items(user_id, course_id, parsed: dict):
    """Import tests and assignments extracted from a syllabus."""
    if not isinstance(parsed, dict):
        return
    for test in parsed.get("tests", []):
        if test.get("title"):
            db.add_test(
                user_id, course_id,
                test["title"], test.get("date"),
                test.get("type", "exam"),
                test.get("topics", ""),
                test.get("weight", ""),
                "syllabus"
            )
    for a in parsed.get("assignments", []):
        if a.get("title"):
            db.upsert_assignment(
                user_id, course_id,
                f"syllabus_{hash(a['title'])}",
                a["title"], a.get("description", ""),
                "", a.get("due_date"),
                a.get("points"), "assignment"
            )


# ── Assignments ───────────────────────────────────────────────────────────────
@app.route("/api/assignments", methods=["GET"])
@token_required
def get_assignments():
    course_id = request.args.get("course_id")
    status = request.args.get("status")
    assignments = db.get_assignments(g.user["id"], course_id, status)
    return jsonify([dict(a) for a in assignments])

@app.route("/api/assignments", methods=["POST"])
@token_required
def add_assignment():
    data = request.json or {}
    aid = db.add_assignment_manual(
        g.user["id"], data.get("course_id"),
        data.get("title", "New Assignment"),
        data.get("description", ""),
        data.get("due_date"), data.get("points"),
        data.get("assignment_type", "assignment")
    )
    return jsonify({"id": aid}), 201

@app.route("/api/assignments/<int:aid>", methods=["GET"])
@token_required
def get_assignment(aid):
    a = db.get_assignment(aid, g.user["id"])
    if not a:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(a))

@app.route("/api/assignments/<int:aid>/draft", methods=["POST"])
@token_required
def generate_draft(aid):
    a = db.get_assignment(aid, g.user["id"])
    if not a:
        return jsonify({"error": "Not found"}), 404
    draft = draft_assignment(
        a["title"],
        a["instructions"] or a["description"] or "",
        a.get("course_name", "")
    )
    db.save_ai_draft(aid, g.user["id"], draft)
    return jsonify({"draft": draft})

@app.route("/api/assignments/<int:aid>/save", methods=["POST"])
@token_required
def save_assignment(aid):
    data = request.json or {}
    db.save_user_edits(
        aid, g.user["id"],
        data.get("content", ""),
        data.get("mark_done", False)
    )
    return jsonify({"message": "Saved"})

@app.route("/api/assignments/<int:aid>/auto_draft", methods=["POST"])
@token_required
def auto_draft(aid):
    """Background-style: generate draft and mark ready — called by scheduler."""
    a = db.get_assignment(aid, g.user["id"])
    if not a or a["status"] != "pending":
        return jsonify({"skipped": True})
    draft = draft_assignment(a["title"], a["instructions"] or a["description"] or "", a.get("course_name", ""))
    db.save_ai_draft(aid, g.user["id"], draft)
    return jsonify({"draft": draft, "assignment_id": aid})


# ── Tests ─────────────────────────────────────────────────────────────────────
@app.route("/api/tests", methods=["GET"])
@token_required
def get_tests():
    upcoming = request.args.get("upcoming") == "true"
    tests = db.get_tests(g.user["id"], upcoming)
    return jsonify([dict(t) for t in tests])

@app.route("/api/tests", methods=["POST"])
@token_required
def add_test():
    data = request.json or {}
    tid = db.add_test(
        g.user["id"], data.get("course_id"),
        data.get("title", ""), data.get("test_date"),
        data.get("test_type", "exam"),
        data.get("topics", ""), data.get("notes", ""), "manual"
    )
    return jsonify({"id": tid}), 201


# ── Syllabus upload ───────────────────────────────────────────────────────────
@app.route("/api/syllabi", methods=["GET"])
@token_required
def get_syllabi():
    syllabi = db.get_syllabi(g.user["id"])
    return jsonify([dict(s) for s in syllabi])

@app.route("/api/syllabi/upload", methods=["POST"])
@token_required
def upload_syllabus():
    course_id = request.form.get("course_id")
    if not course_id:
        return jsonify({"error": "course_id required"}), 400
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    filename = f.filename
    ext = os.path.splitext(filename)[-1].lower()
    if ext not in (".pdf", ".txt", ".doc", ".docx"):
        return jsonify({"error": "Only PDF, TXT, DOC, DOCX allowed"}), 400
    save_path = os.path.join(UPLOAD_DIR, f"{secrets.token_hex(8)}_{filename}")
    f.save(save_path)
    raw_text = extract_text(save_path)
    parsed = parse_syllabus(raw_text)
    sid = db.save_syllabus(g.user["id"], int(course_id), filename, raw_text, json.dumps(parsed))
    _import_syllabus_items(g.user["id"], int(course_id), parsed)
    return jsonify({"id": sid, "parsed": parsed}), 201


# ── Dashboard stats ───────────────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
@token_required
def stats():
    return jsonify(db.get_dashboard_stats(g.user["id"]))

@app.route("/api/study_plan", methods=["GET"])
@token_required
def study_plan():
    tests = [dict(t) for t in db.get_tests(g.user["id"], upcoming_only=True)]
    assignments = [dict(a) for a in db.get_assignments(g.user["id"])]
    plan = suggest_study_plan(tests, assignments)
    return jsonify({"plan": plan})

@app.route("/api/calendar", methods=["GET"])
@token_required
def calendar_events():
    """Return all events (assignments + tests) for the calendar view."""
    assignments = db.get_assignments(g.user["id"])
    tests = db.get_tests(g.user["id"])
    events = []
    for a in assignments:
        if a["due_date"]:
            color = {"pending": "#f59e0b", "ai_ready": "#3b82f6", "done": "#10b981"}.get(a["status"], "#6b7280")
            events.append({
                "id": f"a_{a['id']}", "type": "assignment",
                "title": a["title"], "course": a.get("course_name", ""),
                "date": a["due_date"][:10], "color": color,
                "status": a["status"], "assignment_id": a["id"]
            })
    for t in tests:
        if t["test_date"]:
            events.append({
                "id": f"t_{t['id']}", "type": "test",
                "title": t["title"], "course": t.get("course_name", ""),
                "date": t["test_date"][:10] if t["test_date"] else None,
                "color": "#ef4444", "test_type": t["test_type"],
                "topics": t.get("topics", "")
            })
    return jsonify(events)


# ── Background scheduler ──────────────────────────────────────────────────────
def auto_sync_all_users():
    """Called every 3 hours — syncs BB for all users and auto-drafts assignments."""
    print(f"[{datetime.now()}] Running auto-sync for all users...")
    users = db.get_all_users_with_bb()
    for user in users:
        try:
            _do_sync(user)
            # Auto-draft pending assignments
            pending = db.get_assignments(user["id"], status="pending")
            for a in pending[:5]:  # max 5 per cycle to avoid API rate limits
                try:
                    draft = draft_assignment(
                        a["title"],
                        a["instructions"] or a["description"] or "",
                        a.get("course_name", "")
                    )
                    db.save_ai_draft(a["id"], user["id"], draft)
                    print(f"  ✓ Drafted: {a['title']}")
                except Exception as e:
                    print(f"  ✗ Draft failed for {a['title']}: {e}")
        except Exception as e:
            print(f"  ✗ Sync failed for user {user['id']}: {e}")


if __name__ == "__main__":
    db.init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_sync_all_users, "interval", hours=3, id="auto_sync")
    scheduler.start()
    print("✅ BB Assistant server running on http://localhost:5000")
    print("   Background sync every 3 hours.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
