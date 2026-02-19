# Import vendors so they register into PARSERS
from .pdf import vendors  # noqa: F401
from .xlsx import vendors  # noqa: F401

from .dispatch import parse_file  # noqa: E402
