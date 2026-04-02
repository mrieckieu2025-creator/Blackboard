import sqlite3
import json
from datetime import datetime
from config import DATABASE_PATH


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bb_url TEXT,
            bb_username TEXT,
            bb_password_enc TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bb_course_id TEXT,
            name TEXT NOT NULL,
            code TEXT,
            instructor TEXT,
            term TEXT,
            last_synced TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            bb_assignment_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            instructions TEXT,
            due_date TEXT,
            points_possible REAL,
            assignment_type TEXT DEFAULT 'assignment',
            status TEXT DEFAULT 'pending',
            ai_draft TEXT,
            user_edits TEXT,
            last_synced TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            test_date TEXT,
            test_type TEXT DEFAULT 'exam',
            topics TEXT,
            notes TEXT,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS syllabi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            filename TEXT,
            raw_text TEXT,
            parsed_json TEXT,
            uploaded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    conn.commit()
    conn.close()


# ─── Users ────────────────────────────────────────────────────────────────────

def create_user(username, email, password_hash):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
            (username, email, password_hash)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def get_user_by_username(username):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()


def update_user_blackboard(user_id, bb_url, bb_username, bb_password_enc):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET bb_url=?, bb_username=?, bb_password_enc=? WHERE id=?",
            (bb_url, bb_username, bb_password_enc, user_id)
        )
        conn.commit()
    finally:
        conn.close()


# ─── Courses ──────────────────────────────────────────────────────────────────

def upsert_course(user_id, bb_course_id, name, code, instructor="", term=""):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM courses WHERE user_id=? AND bb_course_id=?",
            (user_id, bb_course_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE courses SET name=?, code=?, instructor=?, term=?, last_synced=datetime('now') WHERE id=?",
                (name, code, instructor, term, existing["id"])
            )
            course_id = existing["id"]
        else:
            conn.execute(
                "INSERT INTO courses (user_id, bb_course_id, name, code, instructor, term, last_synced) VALUES (?,?,?,?,?,?,datetime('now'))",
                (user_id, bb_course_id, name, code, instructor, term)
            )
            course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return course_id
    finally:
        conn.close()


def get_courses(user_id):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM courses WHERE user_id=? ORDER BY name", (user_id,)
        ).fetchall()
    finally:
        conn.close()


def get_course(course_id, user_id):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM courses WHERE id=? AND user_id=?", (course_id, user_id)
        ).fetchone()
    finally:
        conn.close()


def add_course_manual(user_id, name, code, instructor="", term=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO courses (user_id, bb_course_id, name, code, instructor, term) VALUES (?,?,?,?,?,?)",
            (user_id, f"manual_{int(datetime.now().timestamp())}", name, code, instructor, term)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


# ─── Assignments ──────────────────────────────────────────────────────────────

def upsert_assignment(user_id, course_id, bb_assignment_id, title, description="",
                      instructions="", due_date=None, points=None, atype="assignment"):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id, status FROM assignments WHERE user_id=? AND bb_assignment_id=?",
            (user_id, bb_assignment_id)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE assignments SET title=?, description=?, instructions=?,
                   due_date=?, points_possible=?, assignment_type=?, last_synced=datetime('now')
                   WHERE id=?""",
                (title, description, instructions, due_date, points, atype, existing["id"])
            )
            aid = existing["id"]
        else:
            conn.execute(
                """INSERT INTO assignments
                   (user_id, course_id, bb_assignment_id, title, description, instructions,
                    due_date, points_possible, assignment_type, status, last_synced)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (user_id, course_id, bb_assignment_id, title, description, instructions,
                 due_date, points, atype, "pending")
            )
            aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return aid
    finally:
        conn.close()


def get_assignments(user_id, course_id=None, status=None):
    conn = get_db()
    try:
        query = """
            SELECT a.*, c.name as course_name, c.code as course_code
            FROM assignments a
            JOIN courses c ON a.course_id = c.id
            WHERE a.user_id=?
        """
        params = [user_id]
        if course_id:
            query += " AND a.course_id=?"
            params.append(course_id)
        if status:
            query += " AND a.status=?"
            params.append(status)
        query += " ORDER BY a.due_date ASC"
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def get_assignment(assignment_id, user_id):
    conn = get_db()
    try:
        return conn.execute(
            """SELECT a.*, c.name as course_name, c.code as course_code
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               WHERE a.id=? AND a.user_id=?""",
            (assignment_id, user_id)
        ).fetchone()
    finally:
        conn.close()


def save_ai_draft(assignment_id, user_id, draft):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE assignments SET ai_draft=?, status='ai_ready' WHERE id=? AND user_id=?",
            (draft, assignment_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def save_user_edits(assignment_id, user_id, edits, mark_done=False):
    status = "done" if mark_done else "ai_ready"
    conn = get_db()
    try:
        conn.execute(
            "UPDATE assignments SET user_edits=?, status=? WHERE id=? AND user_id=?",
            (edits, status, assignment_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def add_assignment_manual(user_id, course_id, title, description="", due_date=None, points=None, atype="assignment"):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO assignments
               (user_id, course_id, bb_assignment_id, title, description, due_date,
                points_possible, assignment_type, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, course_id, f"manual_{int(datetime.now().timestamp())}",
             title, description, due_date, points, atype, "pending")
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def get_dashboard_stats(user_id):
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM assignments WHERE user_id=?", (user_id,)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM assignments WHERE user_id=? AND status='pending'", (user_id,)).fetchone()[0]
        ai_ready = conn.execute("SELECT COUNT(*) FROM assignments WHERE user_id=? AND status='ai_ready'", (user_id,)).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM assignments WHERE user_id=? AND status='done'", (user_id,)).fetchone()[0]
        upcoming_tests = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE user_id=? AND test_date >= date('now')", (user_id,)
        ).fetchone()[0]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE user_id=? AND due_date < datetime('now') AND status != 'done'",
            (user_id,)
        ).fetchone()[0]
        return {
            "total": total, "pending": pending, "ai_ready": ai_ready,
            "done": done, "upcoming_tests": upcoming_tests, "overdue": overdue
        }
    finally:
        conn.close()


# ─── Tests ────────────────────────────────────────────────────────────────────

def add_test(user_id, course_id, title, test_date, test_type="exam", topics="", notes="", source="manual"):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO tests (user_id, course_id, title, test_date, test_type, topics, notes, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, course_id, title, test_date, test_type, topics, notes, source)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def get_tests(user_id, upcoming_only=False):
    conn = get_db()
    try:
        query = """
            SELECT t.*, c.name as course_name, c.code as course_code
            FROM tests t JOIN courses c ON t.course_id = c.id
            WHERE t.user_id=?
        """
        if upcoming_only:
            query += " AND t.test_date >= date('now')"
        query += " ORDER BY t.test_date ASC"
        return conn.execute(query, (user_id,)).fetchall()
    finally:
        conn.close()


# ─── Syllabi ─────────────────────────────────────────────────────────────────

def save_syllabus(user_id, course_id, filename, raw_text, parsed_json=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO syllabi (user_id, course_id, filename, raw_text, parsed_json) VALUES (?,?,?,?,?)",
            (user_id, course_id, filename, raw_text, parsed_json)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def get_syllabi(user_id):
    conn = get_db()
    try:
        return conn.execute(
            """SELECT s.*, c.name as course_name FROM syllabi s
               JOIN courses c ON s.course_id = c.id
               WHERE s.user_id=? ORDER BY s.uploaded_at DESC""",
            (user_id,)
        ).fetchall()
    finally:
        conn.close()
