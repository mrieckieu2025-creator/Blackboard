import os
from dotenv import load_dotenv

load_dotenv()

# Flask
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(32).hex())
DATABASE_PATH = os.environ.get("DATABASE_PATH", "bb_assistant.db")

# Anthropic / Claude
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# App settings
UPLOAD_FOLDER = "uploads"
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}
