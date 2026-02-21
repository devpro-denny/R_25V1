
import contextvars

# Context variable to store the current user ID
# This allows logs generated deep in the call stack to know which user they belong to
user_id_var = contextvars.ContextVar("user_id", default=None)

# Context variable to store bot category for log isolation routing.
# Values: conservative, scalping, risefall, system
bot_type_var = contextvars.ContextVar("bot_type", default="system")
