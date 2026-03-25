import json
from pathlib import Path
from typing import Dict, Any


def load_feature_file(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Feature file not found: {path}")
    # Support UTF-8 files with optional BOM from Windows editors.
    return json.loads(p.read_text(encoding="utf-8-sig"))
