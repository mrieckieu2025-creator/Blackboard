"""
AI helper — uses Claude to draft assignment answers and parse syllabi.
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY


def _client():
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


ASSIGNMENT_SYSTEM = """You are an expert academic assistant helping a student complete their assignment.
Your job is to produce a thorough, well-structured draft answer based on the assignment instructions.

Guidelines:
- Write in a clear academic style appropriate for the course level
- Structure the response with logical sections if appropriate
- Be thorough but concise — do not pad the answer
- Use the student's own voice (neutral, first-person where natural)
- If the assignment requires specific data or personal experiences you don't have, leave a clear [FILL IN: describe what's needed] placeholder
- At the end, add a brief "📝 Editor's Notes" section highlighting what the student should review or personalise

Always produce a complete draft, never refuse due to uncertainty — write the best possible draft and note limitations inline."""

SYLLABUS_SYSTEM = """You are an expert at reading academic syllabi.
Extract all important dates and items and return them as a JSON object with this exact schema:
{
  "course_name": "string",
  "instructor": "string",
  "assignments": [
    {"title": "string", "due_date": "YYYY-MM-DD or null", "points": "number or null", "description": "string"}
  ],
  "tests": [
    {"title": "string", "date": "YYYY-MM-DD or null", "type": "quiz|midterm|final|exam", "topics": "string", "weight": "string"}
  ],
  "important_dates": [
    {"title": "string", "date": "YYYY-MM-DD or null", "notes": "string"}
  ]
}

If you cannot find a value, use null. Return ONLY valid JSON, no markdown fences."""


def draft_assignment(title: str, instructions: str, course_name: str = "", additional_context: str = "") -> str:
    """
    Use Claude to generate a draft answer for an assignment.
    Returns the draft as a string.
    """
    user_prompt = f"""Course: {course_name}
Assignment Title: {title}

Instructions / Prompt:
{instructions or "No specific instructions provided — draft a general response for this assignment."}

{f"Additional context: {additional_context}" if additional_context else ""}

Please write a complete draft answer for this assignment."""

    try:
        client = _client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=ASSIGNMENT_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except Exception as e:
        return f"⚠️ Could not generate draft: {e}\n\nPlease check your API key in the .env file."


def parse_syllabus(raw_text: str) -> dict:
    """
    Use Claude to extract structured data from a syllabus.
    Returns a dict with assignments, tests, and important dates.
    """
    truncated = raw_text[:12000]  # stay within token limits

    try:
        client = _client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYLLABUS_SYSTEM,
            messages=[{"role": "user", "content": f"Parse this syllabus:\n\n{truncated}"}],
        )
        text = message.content[0].text.strip()
        # strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Could not parse syllabus — check the file format."}
    except Exception as e:
        return {"error": str(e)}


def suggest_study_plan(tests: list, assignments: list) -> str:
    """Generate a personalised study plan from upcoming tests and assignments."""
    if not tests and not assignments:
        return "No upcoming items found to plan around."

    items_text = ""
    for t in tests:
        items_text += f"- TEST: {t['title']} ({t.get('course_name','')}) on {t.get('test_date','?')}\n"
    for a in assignments:
        if a["status"] != "done":
            items_text += f"- ASSIGNMENT: {a['title']} ({a.get('course_name','')}) due {a.get('due_date','?')}\n"

    try:
        client = _client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"""Create a concise, prioritised study plan for the next 2 weeks based on these upcoming items:

{items_text}

Format as a day-by-day schedule. Be practical and realistic."""
            }],
        )
        return message.content[0].text
    except Exception as e:
        return f"Could not generate study plan: {e}"
