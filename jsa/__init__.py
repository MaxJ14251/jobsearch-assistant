"""Agentic job search assistant."""

__version__ = "0.1.0"

# Load .env before anything reads os.environ, so `NVIDIA_API_KEY` works whether
# it comes from the shell or the file. A real environment variable wins.
from .config import load_dotenv as _load_dotenv

_load_dotenv()
