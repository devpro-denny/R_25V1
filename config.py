"""
Compatibility shim.
Conservative config moved to `conservative_strategy/config.py`.
"""

import sys
from conservative_strategy import config as _config

sys.modules[__name__] = _config
