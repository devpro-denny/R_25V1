"""
Compatibility shim.
Conservative strategy core moved to `conservative_strategy/strategy.py`.
"""

import sys
from conservative_strategy import strategy as _strategy

sys.modules[__name__] = _strategy
