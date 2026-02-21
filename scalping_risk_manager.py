"""
Compatibility shim.
Scalping risk manager moved to `scalping_strategy/risk_manager.py`.
"""

import sys
from scalping_strategy import risk_manager as _scalping_risk

sys.modules[__name__] = _scalping_risk
