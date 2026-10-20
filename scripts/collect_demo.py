"""Copy only reviewed synthetic E2E screenshots; no image editing or network."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/images/demo"
# Fixed sources are produced by real browser flows in the checked-in tests.
SHOTS = [
    ("login.png", "screenshots/login-1440.png", "workspace.spec.ts", "登录，凭据未填入"),
    ("workbench.png", "s4-2/dashboard-today-1440.png", "workbench.spec.ts", "工作台，合成申请与今日行动"),
    ("resumes.png", "s4-2/mvp-resume-1440.png", "workbench.spec.ts", "合成简历详情与文本解析状态"),
    ("candidates-mock.png", "screenshots/extraction-candidates-1440.png", "extraction.spec.ts", "Mock 候选、服务器定位及证据，非真实模型输出"),
    ("profile.png", "s4-2/mvp-profile-manual-1440.png", "workbench.spec.ts", "手工保存的合成正式画像"),
    ("jobs.png", "screenshots/jobs-list-1440.png", "jobs.spec.ts", "合成职位列表与审核状态"),
    ("jd-review.png", "s4-2/mvp-jd-reviewed-1440.png", "workbench.spec.ts", "用户审核后的 JD 条件"),
    ("matching.png", "s4-2/mvp-match-evidence-1440.png", "workbench.spec.ts", "纯规则匹配与双侧证据"),
    ("application.png", "s4-2/mvp-application-1440.png", "workbench.spec.ts", "合成申请、日期行动和时间线"),
    ("export.png", "s5-2/export-ready-1440.png", "account-lifecycle.spec.ts", "导出就绪，密码框已清空"),
    ("workbench-mobile.png", "s4-2/dashboard-today-390.png", "workbench.spec.ts", "390px 工作台"),
]


def metadata(data: bytes) -> dict:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("Expected an E2E PNG")
    width, height = struct.unpack(">II", data[16:24])
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "width": width, "height": height}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify published files against their manifest without reading artifacts")
    args = parser.parse_args()
    manifest_path = OUTPUT / "manifest.json"
    if args.check:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != "offerpilot-demo-screenshots/1.0":
            raise ValueError("Unexpected manifest version")
        if {row["file"] for row in manifest["screenshots"]} != {row[0] for row in SHOTS}:
            raise ValueError("Screenshot inventory differs")
        for row in manifest["screenshots"]:
            if Path(row["file"]).name != row["file"]:
                raise ValueError("Unexpected screenshot path")
            expected = {key: row[key] for key in ("sha256", "bytes", "width", "height")}
            if metadata((OUTPUT / row["file"]).read_bytes()) != expected:
                raise ValueError("Published screenshot differs from reviewed manifest")
        print(f"Verified {len(SHOTS)} synthetic screenshots.")
        return
    # Read and validate the complete set before changing published images.
    pending = [(name, source, spec, label, (ROOT / "frontend/artifacts" / source).read_bytes()) for name, source, spec, label in SHOTS]
    rows = [{"file": name, "source": f"frontend/artifacts/{source}", "test": f"frontend/e2e/{spec}", "description": label, **metadata(data)} for name, source, spec, label, data in pending]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, _, _, _, data in pending:
        (OUTPUT / name).write_bytes(data)
    manifest_path.write_text(json.dumps({
        "schema_version": "offerpilot-demo-screenshots/1.0",
        "source_files_modified_at_utc": {
            source: datetime.fromtimestamp((ROOT / "frontend/artifacts" / source).stat().st_mtime, timezone.utc).isoformat()
            for _, source, _, _, _ in pending
        },
        "data": "synthetic-only; Mock candidates are not DeepSeek outputs",
        "screenshots": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(rows)} fixed synthetic E2E screenshots. Review images before publishing.")


if __name__ == "__main__":
    main()
