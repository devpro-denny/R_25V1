"""
Strategy Registry
Central registry mapping strategy names to implementation classes
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Import strategy classes from isolated packages
from conservative_strategy import ConservativeStrategy, ConservativeRiskManager
from scalping_strategy.strategy_external import ScalpingStrategy as ExternalScalpingStrategy
from scalping_strategy.risk_manager import ScalpingRiskManager
from risefallbot import RiseFallStrategy, RiseFallRiskManager


# Strategy registry mapping name -> (Strategy class, RiskManager class)
STRATEGY_REGISTRY = {
    "Conservative": (ConservativeStrategy, ConservativeRiskManager),
    "Scalping": (ExternalScalpingStrategy, ScalpingRiskManager),
    "RiseFall": (RiseFallStrategy, RiseFallRiskManager),
}


def normalize_strategy_name(strategy_name: str = "Conservative") -> str:
    """
    Normalize strategy aliases/casing to canonical registry keys.
    """
    if not strategy_name:
        return "Conservative"

    raw = str(strategy_name).strip()
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    aliases = {
        "conservative": "Conservative",
        "scalping": "Scalping",
        "scalp": "Scalping",
        "risefall": "RiseFall",
        "rf": "RiseFall",
    }
    return aliases.get(key, raw)


def get_strategy(strategy_name: str = "Conservative", respect_feature_flags: bool = True):
    """
    Get strategy and risk manager classes for a given strategy name.

    Args:
        strategy_name: Name of the strategy.
        respect_feature_flags: When True, disabled strategies fall back to Conservative.

    Returns:
        Tuple of (Strategy class, RiskManager class).
        Returns Conservative as safe default if unknown strategy.
    """
    strategy_name = normalize_strategy_name(strategy_name)

    if respect_feature_flags:
        # Check if scalping is enabled
        if strategy_name == "Scalping":
            scalping_enabled = os.getenv("SCALPING_BOT_ENABLED", "false").lower() == "true"
            if not scalping_enabled:
                logger.warning("Scalping bot is disabled (SCALPING_BOT_ENABLED=false), falling back to Conservative")
                return STRATEGY_REGISTRY["Conservative"]

        if strategy_name == "RiseFall":
            rf_enabled = os.getenv("RISE_FALL_BOT_ENABLED", "false").lower() == "true"
            if not rf_enabled:
                logger.warning("Rise/Fall bot is disabled (RISE_FALL_BOT_ENABLED=false), falling back to Conservative")
                return STRATEGY_REGISTRY["Conservative"]

    # Look up strategy
    if strategy_name not in STRATEGY_REGISTRY:
        logger.warning(f"Unknown strategy '{strategy_name}', defaulting to Conservative")
        return STRATEGY_REGISTRY["Conservative"]

    strategy_class, risk_manager_class = STRATEGY_REGISTRY[strategy_name]
    logger.info(f"Strategy loaded: {strategy_name}")

    return strategy_class, risk_manager_class


def get_available_strategies():
    """
    Get list of available strategy names.

    Returns:
        List of strategy names
    """
    strategies = list(STRATEGY_REGISTRY.keys())

    # Filter out Scalping if not enabled
    scalping_enabled = os.getenv("SCALPING_BOT_ENABLED", "false").lower() == "true"
    if not scalping_enabled and "Scalping" in strategies:
        strategies.remove("Scalping")

    # Filter out RiseFall if not enabled
    rf_enabled = os.getenv("RISE_FALL_BOT_ENABLED", "false").lower() == "true"
    if not rf_enabled and "RiseFall" in strategies:
        strategies.remove("RiseFall")

    return strategies
