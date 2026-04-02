"""
Blackboard integration — tries the REST API first, falls back to Playwright.
"""
import json
import requests
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class BlackboardClient:
    def __init__(self, bb_url: str, username: str, password: str):
        self.bb_url = bb_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.token = None
        self._api_available = False

    # ─── Authentication ───────────────────────────────────────────────────────

    def login(self) -> bool:
        """Try API login, fall back to browser session."""
        if self._try_api_login():
            self._api_available = True
            return True
        return self._try_browser_login()

    def _try_api_login(self) -> bool:
        try:
            resp = self.session.post(
                f"{self.bb_url}/learn/api/public/v1/oauth2/token",
                data={
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.password,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                self.token = resp.json().get("access_token")
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                return True
        except Exception:
            pass
        return False

    def _try_browser_login(self) -> bool:
        """Use Playwright to log in via browser."""
        if not PLAYWRIGHT_AVAILABLE:
            return False
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(f"{self.bb_url}/webapps/login/")
                page.fill("#user_id", self.username)
                page.fill("#password", self.password)
                page.click("#entry-login")
                page.wait_for_load_state("networkidle", timeout=15000)
                # grab cookies
                cookies = page.context.cookies()
                for c in cookies:
                    self.session.cookies.set(c["name"], c["value"])
                browser.close()
                return True
        except Exception:
            return False

    # ─── Courses ─────────────────────────────────────────────────────────────

    def get_courses(self):
        """Return list of dicts: {bb_course_id, name, code, instructor, term}"""
        if self._api_available:
            return self._get_courses_api()
        return self._get_courses_scrape()

    def _get_courses_api(self):
        try:
            resp = self.session.get(
                f"{self.bb_url}/learn/api/public/v3/users/{self.username}/courses",
                params={"limit": 100},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data = resp.json().get("results", [])
            courses = []
            for item in data:
                course = item.get("course", {})
                courses.append({
                    "bb_course_id": course.get("id", ""),
                    "name": course.get("name", "Unknown Course"),
                    "code": course.get("courseId", ""),
                    "instructor": "",
                    "term": course.get("term", {}).get("name", "") if isinstance(course.get("term"), dict) else "",
                })
            return courses
        except Exception:
            return []

    def _get_courses_scrape(self):
        if not PLAYWRIGHT_AVAILABLE:
            return []
        try:
            courses = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context()
                # restore session cookies
                ctx.add_cookies([
                    {"name": k, "value": v, "domain": self.bb_url.split("//")[-1], "path": "/"}
                    for k, v in self.session.cookies.items()
                ])
                page = ctx.new_page()
                page.goto(f"{self.bb_url}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1")
                page.wait_for_load_state("networkidle", timeout=15000)
                items = page.query_selector_all("a[href*='course_id']")
                for item in items:
                    href = item.get_attribute("href") or ""
                    name = item.inner_text().strip()
                    if "course_id=" in href and name:
                        course_id = href.split("course_id=")[-1].split("&")[0]
                        courses.append({
                            "bb_course_id": course_id,
                            "name": name,
                            "code": "",
                            "instructor": "",
                            "term": "",
                        })
                browser.close()
            return courses
        except Exception:
            return []

    # ─── Assignments ─────────────────────────────────────────────────────────

    def get_assignments(self, bb_course_id: str):
        """Return list of assignment dicts for a given course."""
        if self._api_available:
            return self._get_assignments_api(bb_course_id)
        return self._get_assignments_scrape(bb_course_id)

    def _get_assignments_api(self, bb_course_id: str):
        assignments = []
        endpoints = [
            f"{self.bb_url}/learn/api/public/v1/courses/{bb_course_id}/contents",
            f"{self.bb_url}/learn/api/public/v1/courses/{bb_course_id}/gradebook/columns",
        ]
        for url in endpoints:
            try:
                resp = self.session.get(url, params={"limit": 200}, timeout=15)
                if resp.status_code != 200:
                    continue
                for item in resp.json().get("results", []):
                    title = item.get("title") or item.get("name", "Untitled")
                    body = item.get("body") or item.get("description") or ""
                    due = item.get("availability", {}).get("adaptiveRelease", {}).get("end") \
                          or item.get("due")
                    if due and "T" in due:
                        due = due[:16].replace("T", " ")
                    points = item.get("score", {}).get("possible") \
                             if isinstance(item.get("score"), dict) else item.get("points")
                    atype = item.get("contentHandler", {}).get("id", "assignment") \
                            if isinstance(item.get("contentHandler"), dict) else "assignment"
                    assignments.append({
                        "bb_assignment_id": item.get("id", ""),
                        "title": title,
                        "description": body[:500] if body else "",
                        "instructions": body,
                        "due_date": due,
                        "points_possible": points,
                        "assignment_type": atype,
                    })
            except Exception:
                continue
        return assignments

    def _get_assignments_scrape(self, bb_course_id: str):
        if not PLAYWRIGHT_AVAILABLE:
            return []
        assignments = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context()
                ctx.add_cookies([
                    {"name": k, "value": v, "domain": self.bb_url.split("//")[-1], "path": "/"}
                    for k, v in self.session.cookies.items()
                ])
                page = ctx.new_page()
                page.goto(
                    f"{self.bb_url}/webapps/blackboard/content/listContent.jsp?course_id={bb_course_id}&content_id=_1_1"
                )
                page.wait_for_load_state("networkidle", timeout=15000)
                links = page.query_selector_all("a[href*='content_id']")
                for link in links:
                    title = link.inner_text().strip()
                    href = link.get_attribute("href") or ""
                    if title and "content_id=" in href:
                        cid = href.split("content_id=")[-1].split("&")[0]
                        assignments.append({
                            "bb_assignment_id": cid,
                            "title": title,
                            "description": "",
                            "instructions": "",
                            "due_date": None,
                            "points_possible": None,
                            "assignment_type": "assignment",
                        })
                browser.close()
        except Exception:
            pass
        return assignments

    # ─── Announcements ───────────────────────────────────────────────────────

    def get_announcements(self, bb_course_id: str):
        """Return recent announcements for a course."""
        try:
            resp = self.session.get(
                f"{self.bb_url}/learn/api/public/v1/courses/{bb_course_id}/announcements",
                params={"limit": 20},
                timeout=10,
            )
            if resp.status_code == 200:
                results = []
                for a in resp.json().get("results", []):
                    results.append({
                        "id": a.get("id", ""),
                        "title": a.get("title", "Announcement"),
                        "body": a.get("body", ""),
                        "posted": a.get("created", ""),
                    })
                return results
        except Exception:
            pass
        return []
