"""Dependency-free runner: python tests/run_all.py   (pytest also works: python -m pytest tests)"""
import importlib, os, sys, traceback
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
bad = total = 0
for mod in ("test_gate", "test_building_blocks", "test_alerts"):
    m = importlib.import_module(mod)
    for name in sorted(n for n in dir(m) if n.startswith("test_")):
        total += 1
        try:
            getattr(m, name)(); print(f"PASS {mod}.{name}")
        except Exception:
            bad += 1; print(f"FAIL {mod}.{name}"); traceback.print_exc()
print(f"\n{total - bad}/{total} test functions passed")
sys.exit(1 if bad else 0)
