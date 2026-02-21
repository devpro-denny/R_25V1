"""
Compatibility shim.
Conservative risk wrapper moved to `conservative_strategy/risk_wrapper.py`.
"""

import sys
from conservative_strategy import risk_wrapper as _risk_wrapper

sys.modules[__name__] = _risk_wrapper
