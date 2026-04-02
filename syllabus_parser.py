"""
Extract raw text from uploaded syllabus files (PDF, TXT, DOCX).
"""
import os

def extract_text(filepath: str) -> str:
    """Extract plain text from a file based on extension."""
    ext = os.path.splitext(filepath)[-1].lower()

    if ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext == ".txt":
        return _extract_txt(filepath)
    elif ext in (".doc", ".docx"):
        return _extract_docx(filepath)
    else:
        return ""


def _extract_pdf(filepath: str) -> str:
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        return text.strip()
    except Exception as e:
        return f"[PDF extraction error: {e}]"


def _extract_txt(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        return f"[TXT read error: {e}]"


def _extract_docx(filepath: str) -> str:
    try:
        import docx
        doc = docx.Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs)
    except ImportError:
        # fallback: read as zip XML
        try:
            import zipfile, re
            with zipfile.ZipFile(filepath) as z:
                with z.open("word/document.xml") as f:
                    xml = f.read().decode("utf-8")
            return re.sub(r"<[^>]+>", " ", xml)
        except Exception as e:
            return f"[DOCX read error: {e}]"
    except Exception as e:
        return f"[DOCX read error: {e}]"
