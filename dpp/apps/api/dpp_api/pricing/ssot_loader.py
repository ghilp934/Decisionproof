"""
SSoT Loader + Validator with JSON Schema validation
"""

import json
from pathlib import Path
from typing import Optional
from jsonschema import validate, ValidationError as JsonSchemaValidationError
from .models import PricingSSoTModel


class SSOTLoader:
    """
    Load and validate Pricing SSoT JSON against JSON Schema
    """

    def __init__(self, ssot_path: Path, schema_path: Path):
        self.ssot_path = ssot_path
        self.schema_path = schema_path
        self._ssot: Optional[PricingSSoTModel] = None

    def load(self) -> PricingSSoTModel:
        """
        Load SSoT JSON and validate against JSON Schema

        Raises:
            FileNotFoundError: SSoT file not found
            JsonSchemaValidationError: JSON Schema validation failed
            ValueError: Pydantic validation failed
        """

        # 1. Load JSON Schema
        with open(self.schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        # 2. Load SSoT JSON
        with open(self.ssot_path, 'r', encoding='utf-8') as f:
            ssot_json = json.load(f)

        # 3. Validate against JSON Schema
        try:
            validate(instance=ssot_json, schema=schema)
        except JsonSchemaValidationError as e:
            raise ValueError(f"JSON Schema validation failed: {e.message}") from e

        # 4. Parse into Pydantic model
        ssot = PricingSSoTModel(**ssot_json)

        self._ssot = ssot
        return ssot

    def get_ssot(self) -> PricingSSoTModel:
        """Get loaded SSoT (cached)"""
        if self._ssot is None:
            raise RuntimeError("SSoT not loaded. Call load() first.")
        return self._ssot


# Singleton instance
_ssot_loader: Optional[SSOTLoader] = None


def get_ssot_loader() -> SSOTLoader:
    """Get singleton SSoT loader instance"""
    global _ssot_loader
    if _ssot_loader is None:
        # Default paths
        fixtures_dir = Path(__file__).parent / "fixtures"
        ssot_path = fixtures_dir / "pricing_ssot.json"
        schema_path = fixtures_dir / "pricing_ssot_schema.json"
        _ssot_loader = SSOTLoader(ssot_path, schema_path)
    return _ssot_loader


def load_pricing_ssot() -> PricingSSoTModel:
    """Convenience function to load pricing SSoT"""
    loader = get_ssot_loader()
    return loader.load()
