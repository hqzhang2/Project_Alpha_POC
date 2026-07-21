#!/usr/bin/env python3
"""
Common Configuration Module
Centralized settings for all projects using Pydantic Settings.
"""
import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonConfig(BaseSettings):
    """Base configuration shared by all projects."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment
    env: str = Field(default="QA", description="Environment: QA or PROD")
    log_level: str = Field(default="INFO", description="Logging level")

    # Project paths
    project_root: Path = Field(default_factory=lambda: Path(__file__).parent.parent.parent)
    data_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent.parent / "data")
    logs_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent.parent / "logs")

    # Yahoo Finance
    yfinance_timeout: int = Field(default=30, description="yfinance request timeout (seconds)")
    yfinance_retries: int = Field(default=3, description="yfinance retry attempts")
    yfinance_cache_ttl: int = Field(default=300, description="Quote cache TTL (seconds)")

    # Rate limiting
    yfinance_rate_limit: float = Field(default=0.05, description="Delay between requests (seconds)")

    # API
    api_host: str = Field(default="0.0.0.0", description="API server host")
    cors_origins: list[str] = Field(default=["*"], description="CORS allowed origins")

    # Cache
    cache_type: str = Field(default="memory", description="Cache backend: memory or redis")
    redis_url: Optional[str] = Field(default=None, description="Redis URL if using redis cache")

    # Database (future)
    database_url: Optional[str] = Field(default=None, description="PostgreSQL connection string")


class NSConfig(CommonConfig):
    """Nine Street specific configuration."""

    # NS-3 Sector Rotation
    ns3_port_qa: int = Field(default=9237, description="NS-3 QA port")
    ns3_port_prod: int = Field(default=9236, description="NS-3 PROD port")
    ns3_lookback_weeks: int = Field(default=52, description="Lookback weeks for tier computation")
    ns3_hmm_states: int = Field(default=2, description="HMM number of states")
    ns3_hmm_iter: int = Field(default=500, description="HMM iterations")
    ns3_hmm_bull_threshold: float = Field(default=0.65, description="HMM bull probability threshold")
    ns3_rs_percentile: float = Field(default=0.75, description="RS percentile for stock selection")
    ns3_piotroski_min: int = Field(default=7, description="Minimum Piotroski F-Score")
    ns3_ta_score_min: int = Field(default=3, description="Minimum TA score")

    # NS-4 Ratio Trading
    ns4_port_qa: int = Field(default=9241, description="NS-4 QA port")
    ns4_port_prod: int = Field(default=9240, description="NS-4 PROD port")

    # NS-1 (Alpha Terminal integration)
    ns1_port_qa: int = Field(default=9219, description="NS-1 QA port")
    ns1_port_prod: int = Field(default=9218, description="NS-1 PROD port")

    @property
    def ns3_port(self) -> int:
        return self.ns3_port_qa if self.env == "QA" else self.ns3_port_prod

    @property
    def ns4_port(self) -> int:
        return self.ns4_port_qa if self.env == "QA" else self.ns4_port_prod

    @property
    def ns1_port(self) -> int:
        return self.ns1_port_qa if self.env == "QA" else self.ns1_port_prod


class AlphaConfig(CommonConfig):
    """Alpha Terminal specific configuration."""

    alpha_port_qa: int = Field(default=9099, description="Alpha Terminal QA port")
    alpha_port_prod: int = Field(default=9098, description="Alpha Terminal PROD port")

    @property
    def alpha_port(self) -> int:
        return self.alpha_port_qa if self.env == "QA" else self.alpha_port_prod


class PortalConfig(CommonConfig):
    """Portal configuration."""

    portal_port: int = Field(default=8000, description="Portal port")


# Singleton instances
_ns_config: Optional[NSConfig] = None
_alpha_config: Optional[AlphaConfig] = None
_portal_config: Optional[PortalConfig] = None
_common_config: Optional[CommonConfig] = None


def get_common_config() -> CommonConfig:
    global _common_config
    if _common_config is None:
        _common_config = CommonConfig()
    return _common_config


def get_ns_config() -> NSConfig:
    global _ns_config
    if _ns_config is None:
        _ns_config = NSConfig()
    return _ns_config


def get_alpha_config() -> AlphaConfig:
    global _alpha_config
    if _alpha_config is None:
        _alpha_config = AlphaConfig()
    return _alpha_config


def get_portal_config() -> PortalConfig:
    global _portal_config
    if _portal_config is None:
        _portal_config = PortalConfig()
    return _portal_config


# For backwards compatibility
def get_config(project: str = "common") -> CommonConfig:
    """Get config for a specific project."""
    if project == "ns":
        return get_ns_config()
    elif project == "alpha":
        return get_alpha_config()
    elif project == "portal":
        return get_portal_config()
    return get_common_config()