"""Build src/caneta/data/journals/ from a journal_templates folder (dev-time).

    uv run python scripts/build_journal_data.py C:/Users/<you>/Documents/journal_templates

Copies _INDICE_GERAL.csv and writes templates.json = {"<area>/<pasta>": [{"file", "url"}...]} from the
per-area _manifest.csv files. Templates themselves are NOT copied: CANETA downloads them on demand from
each journal's own site."""

import csv
import json
import re
import shutil
import sys
from pathlib import Path

URL = re.compile(r"https?://[^\s,;)\"']+")


def main(src: Path) -> None:
    out = Path(__file__).resolve().parents[1] / "src" / "caneta" / "data" / "journals"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "_INDICE_GERAL.csv", out / "_INDICE_GERAL.csv")
    index = list(csv.DictReader(open(src / "_INDICE_GERAL.csv", encoding="utf-8-sig")))
    pastas = {(r["area"], r["pasta"]) for r in index}
    templates: dict[str, list[dict]] = {}
    for area_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        manifest = area_dir / "_manifest.csv"
        if not manifest.is_file():
            continue
        for row in csv.DictReader(open(manifest, encoding="utf-8-sig", errors="replace")):
            local = (row.get("local_file") or "").replace("\\", "/")
            urls = URL.findall(row.get("template_source_url") or "")
            for part in [p.strip() for p in local.split(";") if p.strip()]:
                segs = [s for s in part.split("/") if s]
                pasta = next((s for s in segs if (area_dir.name, s) in pastas), None)
                if not pasta or not urls:
                    continue
                templates.setdefault(f"{area_dir.name}/{pasta}", []).append(
                    {"file": segs[-1], "url": urls[0].rstrip(".")})
    (out / "templates.json").write_text(json.dumps(templates, indent=1, ensure_ascii=False), encoding="utf-8")
    covered = sum(1 for r in index if f"{r['area']}/{r['pasta']}" in templates)
    print(f"{len(index)} journals; template URLs for {covered}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
