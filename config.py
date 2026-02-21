"""
Compatibility shim.
Conservative config moved to `conservative_strategy/config.py`.
"""

import sys
from conservative_strategy import config as _config

# Populate this shim module as well, so any stale references captured during
# import cycles still expose the expected config attributes.
for _name in dir(_config):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_config, _name)

# Preserve legacy behavior for normal imports: `import config` resolves to the
# conservative config module object.
sys.modules[__name__] = _config
