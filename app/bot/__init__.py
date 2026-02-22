"""
Bot Module - Multi-User Bot Management

Use lazy attribute loading to avoid import side effects during early startup.
"""

from importlib import import_module

__all__ = [
    "events",
    "telegram_bridge",
    "bot_manager",
    "BotRunner",
    "BotStatus",
    "BotState",
    "event_manager",
]


def __getattr__(name):
    if name == "events":
        module = import_module("app.bot.events")
        globals()[name] = module
        return module

    if name == "telegram_bridge":
        module = import_module("app.bot.telegram_bridge")
        globals()[name] = module
        return module

    if name == "bot_manager":
        value = import_module("app.bot.manager").bot_manager
        globals()[name] = value
        return value

    if name == "BotRunner":
        value = import_module("app.bot.runner").BotRunner
        globals()[name] = value
        return value

    if name == "BotStatus":
        value = import_module("app.bot.runner").BotStatus
        globals()[name] = value
        return value

    if name == "BotState":
        value = import_module("app.bot.state").BotState
        globals()[name] = value
        return value

    if name == "event_manager":
        value = import_module("app.bot.events").event_manager
        globals()[name] = value
        return value

    raise AttributeError(f"module 'app.bot' has no attribute '{name}'")
