"""Dependency-free runner: python tests/run_all.py   (pytest also works: python -m pytest tests)"""
import importlib, os, sys, traceback, unittest
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
bad = total = skipped = 0
for mod in ("test_gate", "test_building_blocks", "test_alerts", "test_service_offline"):
    m = importlib.import_module(mod)
    for name in sorted(n for n in dir(m) if n.startswith("test_")):
        total += 1
        try:
            getattr(m, name)(); print(f"PASS {mod}.{name}")
        except unittest.SkipTest as e:
            skipped += 1; print(f"SKIP {mod}.{name}: {e}")
        except Exception:
            bad += 1; print(f"FAIL {mod}.{name}"); traceback.print_exc()
print(f"\n{total - bad - skipped}/{total} test functions passed" + (f", {skipped} skipped" if skipped else ""))
sys.exit(1 if bad else 0)
