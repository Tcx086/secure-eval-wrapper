"""Source-checkout wrapper for the fixture-default monitoring demo."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from secure_eval_wrapper.monitoring.cli import main
if __name__=="__main__": raise SystemExit(main())