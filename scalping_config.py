"""
Compatibility shim.
Scalping config moved to `scalping_strategy/config.py`.
"""

import sys
from scalping_strategy import config as _scalping_config

sys.modules[__name__] = _scalping_config
