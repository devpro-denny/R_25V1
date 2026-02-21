"""
Compatibility shim.
Conservative risk manager core moved to `conservative_strategy/risk_manager.py`.
"""

import sys
from conservative_strategy import risk_manager as _risk_manager

sys.modules[__name__] = _risk_manager
