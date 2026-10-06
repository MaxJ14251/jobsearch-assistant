"""Agentic job search assistant."""

# The one version number: pyproject.toml reads it, and the User-Agent says it.
# Set before anything below imports config, which reads it.
__version__ = "0.2.0"

# Load .env before anything reads os.environ, so `NVIDIA_API_KEY` works whether
# it comes from the shell or the file. A real environment variable wins.
from .config import load_dotenv as _load_dotenv

_load_dotenv()
