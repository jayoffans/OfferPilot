"""Export only schema metadata; no DB migration, user data, or AI calls."""
import json
import os
from pathlib import Path

os.environ['OFFERPILOT_DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['OFFERPILOT_ENVIRONMENT'] = 'development'
from offerpilot_api.main import create_app  # noqa: E402

target = Path(__file__).resolve().parents[1] / 'openapi.json'
target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('OpenAPI exported (no runtime data).')
