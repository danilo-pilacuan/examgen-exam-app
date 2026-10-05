"""Configuración de exam-app (pydantic-settings, lee .env).

Complementa `core.config` (shared kernel) con lo específico de la capa de
API: orígenes CORS del frontend y URLs de servicios.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Orígenes permitidos para CORS (frontend dev + prod, separados por coma).
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
