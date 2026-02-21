import pytest
import os
from strategy_registry import (
    get_strategy,
    get_available_strategies,
    normalize_strategy_name,
    STRATEGY_REGISTRY,
)

def test_get_strategy_default(mock_env_vars):
    """Test getting default strategy"""
    strat, risk = get_strategy("Unknown")
    assert strat == STRATEGY_REGISTRY["Conservative"][0]
    assert risk == STRATEGY_REGISTRY["Conservative"][1]

def test_get_strategy_conservative(mock_env_vars):
    """Test getting conservative strategy"""
    strat, risk = get_strategy("Conservative")
    assert strat == STRATEGY_REGISTRY["Conservative"][0]

def test_get_strategy_scalping_disabled(monkeypatch, mock_env_vars):
    """Test scalping fallback when disabled"""
    monkeypatch.setenv("SCALPING_BOT_ENABLED", "false")
    strat, risk = get_strategy("Scalping")
    assert strat == STRATEGY_REGISTRY["Conservative"][0]

def test_get_strategy_scalping_enabled(monkeypatch, mock_env_vars):
    """Test getting scalping when enabled"""
    monkeypatch.setenv("SCALPING_BOT_ENABLED", "true")
    strat, risk = get_strategy("Scalping")
    assert strat == STRATEGY_REGISTRY["Scalping"][0]


def test_get_strategy_scalping_lowercase_enabled(monkeypatch, mock_env_vars):
    """Lowercase strategy names should resolve to canonical registry key."""
    monkeypatch.setenv("SCALPING_BOT_ENABLED", "true")
    strat, risk = get_strategy("scalping")
    assert strat == STRATEGY_REGISTRY["Scalping"][0]


def test_normalize_strategy_name_aliases():
    assert normalize_strategy_name("scalping") == "Scalping"
    assert normalize_strategy_name("rise_fall") == "RiseFall"
    assert normalize_strategy_name("Conservative") == "Conservative"

def test_get_available_strategies(monkeypatch, mock_env_vars):
    """Test available strategies list filtering"""
    monkeypatch.setenv("SCALPING_BOT_ENABLED", "true")
    monkeypatch.setenv("RISE_FALL_BOT_ENABLED", "false")
    
    strategies = get_available_strategies()
    assert "Conservative" in strategies
    assert "Scalping" in strategies
    assert "RiseFall" not in strategies
