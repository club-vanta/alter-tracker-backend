#!/usr/bin/env python
"""
Generates openapi.json from the FastAPI app schema.
Run directly or via the pre-commit hook (which also stages the file).

    uv run python scripts/generate_openapi.py
"""

import json
from pathlib import Path

from app.main import app

output = Path(__file__).parent.parent / "openapi.json"
output.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"openapi.json written to {output}")
