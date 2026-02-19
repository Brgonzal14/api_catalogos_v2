# Import all vendor PDF parsers (side-effect registration)
from .vendors_aes import AESPdfParser  # noqa: F401
from .vendors_albany import AlbanyPdfParser  # noqa: F401
from .vendors_diehl import DiehlPdfParser  # noqa: F401
from .vendors_hamilton import HamiltonPdfParser  # noqa: F401
from .vendors_holcim import HolcimPdfParser  # noqa: F401
from .vendors_ipeco import IpecoPdfParser  # noqa: F401
from .vendors_matzen import MatzenPdfParser  # noqa: F401
from .vendors_stabilus import StabilusPdfParser  # noqa: F401
from .vendors_stuker_sac import StukerSacPdfParser  # noqa: F401
from .vendors_stuker_hansair import StukerHansairPdfParser  # noqa: F401
from .vendors_tdi import TDIPdfParser  # noqa: F401
from .vendors_vincorion import VincorionPdfParser  # noqa: F401
