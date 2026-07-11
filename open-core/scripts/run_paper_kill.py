import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from secure_eval_wrapper.paper.cli import kill_main
if __name__=="__main__":raise SystemExit(kill_main())
