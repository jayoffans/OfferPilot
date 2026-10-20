import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.OFFERPILOT_TEST_PYTHON || path.join(root, '..', 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const exported = spawnSync(python, ['-c', 'import json; from offerpilot_api.main import create_app; print(json.dumps(create_app().openapi()))'], {
  cwd: path.join(root, '..'), encoding: 'utf8', env: { ...process.env, OFFERPILOT_DATABASE_URL: 'sqlite:///:memory:', OFFERPILOT_ENVIRONMENT: 'development', PYTHONIOENCODING: 'utf-8' },
});
if (exported.status !== 0) throw new Error('Cannot export backend schema; install locked backend dependencies first.');
const live = JSON.parse(exported.stdout);
const saved = JSON.parse(readFileSync(path.join(root, 'openapi.json'), 'utf8'));
if (JSON.stringify(live) !== JSON.stringify(saved)) throw new Error('OpenAPI differs from backend. Run export-openapi.py then npm run api:generate.');
const generated = astToString(await openapiTS(saved));
const checked = readFileSync(path.join(root, 'src/lib/schema.d.ts'), 'utf8');
// The CLI prepends the generated-file banner.
if (!checked.endsWith(generated)) throw new Error('Generated client types differ. Run npm run api:generate.');
console.log('Backend schema and generated TypeScript are in sync.');
