import json
import sys
from typing import Any, Dict, List

from evaluator import filter_ips_with_latency, to_bool, to_float, to_int


def to_result(success: bool, message: str, groups: List[Dict[str, Any]] = None):
    print(
        json.dumps(
            {
                "success": success,
                "message": message,
                "groups": groups or [],
            },
            ensure_ascii=False,
        )
    )


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        to_result(False, "empty payload")
        return
    try:
        payload = json.loads(raw)
    except Exception as exc:
        to_result(False, f"invalid payload: {exc}")
        return
    groups = payload.get("groups", [])
    if not isinstance(groups, list):
        to_result(False, "groups must be a list")
        return
    out: List[Dict[str, Any]] = []
    try:
        for group in groups:
            if not isinstance(group, dict):
                continue
            items = filter_ips_with_latency(
                ips=[str(x).strip() for x in group.get("ips", []) if str(x).strip()],
                max_count=to_int(group.get("maxCount", 50), 50),
                tcp_check=to_bool(group.get("tcpCheck", True), True),
                http_check=to_bool(group.get("httpCheck", False), False),
                tcp_port=to_int(group.get("tcpPort", 80), 80),
                tcp_timeout=to_float(group.get("tcpTimeout", 0.6), 0.6),
                tcp_threshold=to_int(group.get("tcpThreshold", 190), 190),
                min_latency_ms=to_int(group.get("minLatencyMs", 0), 0),
                http_host=str(group.get("httpHost", "dash.cloudflare.com")).strip() or "dash.cloudflare.com",
                http_timeout=to_float(group.get("httpTimeout", 2.0), 2.0),
                tcp_batch_size=to_int(group.get("tcpBatchSize", 150), 150),
                tcp_max_duration=to_int(group.get("tcpMaxDuration", 600), 600),
                tcp_target_count=to_int(group.get("tcpTargetCount", 20), 20),
                source_network=None,
                download_test_enabled=to_bool(group.get("downloadTestEnabled", False), False),
                download_test_url=str(group.get("downloadTestUrl", "")).strip(),
                download_timeout_seconds=to_int(group.get("downloadTimeoutSeconds", 10), 10),
                download_ranking_mode=str(group.get("downloadRankingMode", "latency_first")).strip() or "latency_first",
                download_top_n=to_int(group.get("downloadTopN", 0), 0),
            )
            out.append(
                {
                    "groupId": str(group.get("groupId", "")).strip(),
                    "items": items,
                }
            )
    except Exception as exc:
        to_result(False, f"evaluate error: {exc}")
        return
    to_result(True, "completed", out)


if __name__ == "__main__":
    main()
