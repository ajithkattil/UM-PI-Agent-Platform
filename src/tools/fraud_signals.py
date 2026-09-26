"""Rule-based fraud/anomaly signal computation (Claims LLD Section 7).

Deliberately NOT an LLM call — computes real numbers from claim history, so
the Fraud Agent has objective evidence to reason over rather than being
asked to spot a pattern in raw history itself. This is what makes the
fraud signal eval-able: "is this provider billing 10x their peer median"
is a checkable fact; "does this look suspicious" is not.

Two signal types:
  1. Volume anomaly — this provider's claim count for a service vs. the
     peer median for the same service, both within FRAUD_LOOKBACK_WINDOW_DAYS.
     PLANTED AND VERIFIED in the synthetic claims data (see
     eval/claims_test_cases.jsonl's claim_flag_siu_volume_anomaly case).
  2. Threshold clustering — claims billed suspiciously close to a known
     review-threshold dollar amount. Implemented, but no synthetic scenario
     currently plants this pattern — NOT yet eval-verified. Flagged here
     rather than silently presented as proven (same discipline as
     BUILD_NOTES.md throughout this project).
"""

import statistics
from src.tools.claims_db_tools import get_provider_claim_history, get_peer_claim_counts
from src import config


def compute_signals(provider_id: str, service_code: str) -> dict:
    history = get_provider_claim_history(provider_id, service_code, config.FRAUD_LOOKBACK_WINDOW_DAYS)
    this_provider_count = len(history)

    peer_counts = get_peer_claim_counts(service_code, config.FRAUD_LOOKBACK_WINDOW_DAYS)
    # Peer baseline excludes this provider — otherwise a high-volume outlier
    # would inflate its own comparison baseline.
    other_counts = [count for pid, count in peer_counts.items() if pid != provider_id]
    peer_median = statistics.median(other_counts) if other_counts else 0

    if peer_median > 0:
        volume_ratio = this_provider_count / peer_median
        volume_anomaly = this_provider_count >= config.FRAUD_VOLUME_THRESHOLD_MULTIPLIER * peer_median
    else:
        # No peer data at all — can't compute a ratio, so don't claim an
        # anomaly from an undefined comparison.
        volume_ratio = None
        volume_anomaly = False

    threshold = config.REVIEW_THRESHOLDS.get(service_code)
    threshold_clustering_count = 0
    if threshold:
        for h in history:
            if abs(float(h["billed_amount"]) - threshold) / threshold <= 0.05:
                threshold_clustering_count += 1

    return {
        "provider_claim_count": this_provider_count,
        "peer_median_claim_count": peer_median,
        "volume_ratio": volume_ratio,
        "volume_anomaly": volume_anomaly,
        "threshold_clustering_count": threshold_clustering_count,
        "lookback_days": config.FRAUD_LOOKBACK_WINDOW_DAYS,
    }
