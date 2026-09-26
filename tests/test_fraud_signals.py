"""Verifies fraud_signals.compute_signals() correctly discriminates between
the planted volume anomaly (Dr. Elena Ruiz, prov-1) and a normal-volume
provider, against the live database — not a mock.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.fraud_signals import compute_signals


def uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def run():
    passed, failed = 0, 0

    signals = compute_signals(uid("prov-1"), "72148")
    ok = signals["volume_anomaly"] is True and signals["volume_ratio"] >= 3.0
    passed += ok
    failed += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] planted_anomaly_detected: {signals}")

    signals2 = compute_signals(uid("prov-2"), "72148")
    ok2 = signals2["volume_anomaly"] is False
    passed += ok2
    failed += not ok2
    print(f"[{'PASS' if ok2 else 'FAIL'}] normal_provider_not_flagged: {signals2}")

    print(f"\n{passed}/{passed + failed} scenarios correct")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
