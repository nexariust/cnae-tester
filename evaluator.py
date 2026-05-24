import ipaddress
import os
import random
import socket
import time
from functools import cmp_to_key
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests


def to_int(value, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def to_float(value, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def to_bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return fallback


def tcping_latency_ms(ip: str, port: int, timeout: float) -> float:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        start = time.time()
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        cost = (time.time() - start) * 1000
        if result == 0:
            return cost
        return -1
    except Exception:
        return -1


def http_redirect_check(ip: str, host: str, timeout: float) -> bool:
    if ":" in ip:
        return False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, 80))
        req = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        sock.sendall(req.encode())
        data = sock.recv(2048).decode(errors="ignore")
        sock.close()
        if not data.startswith("HTTP/"):
            return False
        code = int(data.split()[1])
        if code in (301, 302, 307, 308):
            for line in data.split("\r\n"):
                if line.lower().startswith("location: https://"):
                    return True
    except Exception:
        return False
    return False


def group_by_subnet(ips: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for ip in ips:
        try:
            network = (
                ipaddress.ip_network(f"{ip}/24", strict=False)
                if ":" not in ip
                else ipaddress.ip_network(f"{ip}/64", strict=False)
            )
            key = str(network.network_address)
        except Exception:
            key = ip
        out.setdefault(key, []).append(ip)
    return out


def select_ips_randomly(subnets: Dict[str, List[str]], max_count: int) -> List[str]:
    selected: List[str] = []
    keys = list(subnets.keys())
    random.shuffle(keys)
    while len(selected) < max_count and keys:
        for key in keys[:]:
            if len(selected) >= max_count:
                break
            ips = subnets[key]
            if ips:
                choice = random.choice(ips)
                selected.append(choice)
                subnets[key].remove(choice)
                if not subnets[key]:
                    keys.remove(key)
    return selected


def network_host_range(network: ipaddress._BaseNetwork) -> Tuple[int, int]:
    total = int(network.num_addresses)
    if total <= 0:
        base = int(network.network_address)
        return base, base
    if network.version == 4 and network.prefixlen <= 30:
        first = int(network.network_address) + 1
        last = int(network.broadcast_address) - 1
        if last < first:
            first = int(network.network_address)
            last = int(network.broadcast_address)
        return first, last
    first = int(network.network_address)
    last = int(network.broadcast_address)
    return first, last


def sample_hosts_from_network_flat(network: ipaddress._BaseNetwork, sample_size: int) -> List[str]:
    if sample_size <= 0:
        return []
    first, last = network_host_range(network)
    total_hosts = last - first + 1
    if total_hosts <= 0:
        return [str(network.network_address)]
    if total_hosts <= sample_size:
        return [str(ipaddress.ip_address(v)) for v in range(first, last + 1)]
    picks = random.sample(range(first, last + 1), sample_size)
    return [str(ipaddress.ip_address(v)) for v in picks]


def sample_hosts_from_network_excluding_flat(
    network: ipaddress._BaseNetwork,
    sample_size: int,
    exclude: Set[int],
) -> List[str]:
    if sample_size <= 0:
        return []
    first, last = network_host_range(network)
    total_hosts = last - first + 1
    if total_hosts <= 0:
        return []
    if total_hosts <= sample_size:
        return [str(ipaddress.ip_address(v)) for v in range(first, last + 1) if v not in exclude]
    picked: Set[int] = set()
    out: List[str] = []
    attempts = 0
    max_attempts = max(sample_size * 10, 100)
    while len(out) < sample_size and attempts < max_attempts:
        attempts += 1
        value = random.randint(first, last)
        if value in exclude or value in picked:
            continue
        picked.add(value)
        out.append(str(ipaddress.ip_address(value)))
    return out


def sample_hosts_from_network_multi_24(
    network: ipaddress._BaseNetwork,
    sample_size: int,
    exclude: Optional[Set[int]] = None,
) -> List[str]:
    if sample_size <= 0:
        return []
    if network.version != 4 or network.prefixlen >= 24:
        if exclude is None:
            return sample_hosts_from_network_flat(network, sample_size)
        return sample_hosts_from_network_excluding_flat(network, sample_size, exclude)
    num_subnets = 1 << (24 - network.prefixlen)
    if num_subnets <= 1:
        if exclude is None:
            return sample_hosts_from_network_flat(network, sample_size)
        return sample_hosts_from_network_excluding_flat(network, sample_size, exclude)
    if sample_size <= 1:
        subnet_indices = [random.randrange(num_subnets)]
    else:
        max_subnets = min(sample_size, 16)
        subnet_count = min(num_subnets, max(2, max_subnets))
        subnet_indices = random.sample(range(num_subnets), subnet_count)
    per_subnet = sample_size // len(subnet_indices)
    remainder = sample_size % len(subnet_indices)
    base = int(network.network_address)
    out: List[str] = []
    for idx, subnet_index in enumerate(subnet_indices):
        need = per_subnet + (1 if idx < remainder else 0)
        if need <= 0:
            continue
        subnet_addr = base + subnet_index * 256
        subnet = ipaddress.ip_network((subnet_addr, 24))
        if exclude is None:
            out.extend(sample_hosts_from_network_flat(subnet, need))
        else:
            out.extend(sample_hosts_from_network_excluding_flat(subnet, need, exclude))
    return out


def sample_hosts_from_network_excluding(
    network: ipaddress._BaseNetwork,
    sample_size: int,
    exclude: Set[int],
) -> List[str]:
    if sample_size <= 0:
        return []
    if network.version == 4 and network.prefixlen < 24:
        picked = sample_hosts_from_network_multi_24(network, sample_size, exclude)
        if len(picked) >= sample_size:
            return picked[:sample_size]
        if not picked:
            return sample_hosts_from_network_excluding_flat(network, sample_size, exclude)
        exclude_next = set(exclude)
        for ip in picked:
            try:
                exclude_next.add(int(ipaddress.ip_address(ip)))
            except Exception:
                continue
        remaining = sample_size - len(picked)
        if remaining <= 0:
            return picked
        picked.extend(sample_hosts_from_network_excluding_flat(network, remaining, exclude_next))
        return picked
    return sample_hosts_from_network_excluding_flat(network, sample_size, exclude)


def build_download_target(url: str, ip: str) -> Tuple[str, Dict[str, str]]:
    parts = urlsplit(str(url or "").strip())
    host = parts.hostname or ""
    if not parts.scheme or not host:
        raise ValueError("invalid download url")
    netloc = ip
    if ":" in ip and not ip.startswith("["):
        netloc = f"[{ip}]"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    headers = {"Host": host}
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, parts.fragment)), headers


def read_download_cap_mbps() -> float:
    return max(0.0, to_float(os.getenv("TESTER_MAX_DOWNLOAD_SPEED_MBPS", "0"), 0.0))


def measure_download_speed_mb_per_sec(
    ip: str,
    url: str,
    timeout: float,
    max_speed_mbps: float,
) -> Tuple[Optional[float], bool, str]:
    try:
        target_url, headers = build_download_target(url, ip)
    except Exception as exc:
        return None, False, str(exc)
    chunk_size = 64 * 1024
    started = time.time()
    total_bytes = 0
    try:
        with requests.get(
            target_url,
            headers=headers,
            stream=True,
            timeout=(5, max(1.0, timeout)),
            verify=False,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if max_speed_mbps > 0:
                    expected = total_bytes / (max_speed_mbps * 1024 * 1024)
                    elapsed = time.time() - started
                    if expected > elapsed:
                        time.sleep(expected - elapsed)
                if time.time() - started >= timeout:
                    break
        elapsed = max(time.time() - started, 0.001)
        if total_bytes <= 0:
            return None, False, "no download data"
        return total_bytes / 1024 / 1024 / elapsed, True, ""
    except Exception as exc:
        return None, False, str(exc)


def filter_ips_with_latency(
    ips: List[str],
    max_count: int,
    tcp_check: bool,
    http_check: bool,
    tcp_port: int,
    tcp_timeout: float,
    tcp_threshold: int,
    min_latency_ms: int,
    http_host: str,
    http_timeout: float,
    tcp_batch_size: int,
    tcp_max_duration: int,
    tcp_target_count: int,
    source_network: Optional[ipaddress._BaseNetwork] = None,
    download_test_enabled: bool = False,
    download_test_url: str = "",
    download_timeout_seconds: int = 10,
    download_ranking_mode: str = "latency_first",
    download_top_n: int = 0,
) -> List[Dict[str, object]]:
    unique_ips = list(dict.fromkeys(ips))
    if not unique_ips or max_count <= 0:
        return []

    effective_limit = max_count
    if download_test_enabled and download_top_n > 0:
        effective_limit = min(max_count, download_top_n)

    if not tcp_check and not http_check:
        subnets = group_by_subnet(unique_ips)
        return [
            {
                "ip": ip,
                "latencyMs": None,
                "httpPassed": False,
                "downloadSpeedMBps": None,
                "downloadPassed": False,
            }
            for ip in select_ips_randomly(subnets, effective_limit)
        ]

    pool = set(unique_ips)
    passed: List[str] = []
    latency_map: Dict[str, float] = {}
    http_passed_map: Dict[str, bool] = {}
    download_speed_map: Dict[str, float] = {}
    download_passed_map: Dict[str, bool] = {}
    download_error_map: Dict[str, str] = {}
    tried: Set[str] = set()
    tried_ints: Set[int] = set()
    start = time.time()
    batch_size = max(1, int(tcp_batch_size))
    max_duration = max(1, int(tcp_max_duration))
    target_count = min(max_count, max(1, int(tcp_target_count)))
    min_latency = max(0, int(min_latency_ms))

    while len(passed) < target_count and time.time() - start < max_duration:
        if not pool and source_network is not None:
            refill = sample_hosts_from_network_excluding(source_network, batch_size, tried_ints)
            if not refill:
                break
            for ip in refill:
                if ip not in tried:
                    pool.add(ip)
        if not pool:
            break
        batch = random.sample(list(pool), min(batch_size, len(pool)))
        for ip in batch:
            pool.remove(ip)
            tried.add(ip)
            try:
                tried_ints.add(int(ipaddress.ip_address(ip)))
            except Exception:
                continue
        order: List[str] = []
        for subnet_ips in group_by_subnet(batch).values():
            order.extend(subnet_ips)
        random.shuffle(order)

        for ip in order:
            if len(passed) >= max_count:
                break
            latency = None
            if tcp_check:
                latency = tcping_latency_ms(ip, tcp_port, tcp_timeout)
                if latency < 0 or latency > tcp_threshold or latency < min_latency:
                    continue
                latency_map[ip] = latency
            if http_check:
                http_ok = http_redirect_check(ip, http_host, http_timeout)
                if not http_ok:
                    continue
                http_passed_map[ip] = True
            passed.append(ip)

    max_speed_mbps = read_download_cap_mbps()
    if download_test_enabled and str(download_test_url or "").strip() and http_check:
        for ip in passed:
            if not http_passed_map.get(ip, False):
                continue
            speed, ok, err = measure_download_speed_mb_per_sec(
                ip,
                download_test_url,
                float(max(1, int(download_timeout_seconds))),
                max_speed_mbps,
            )
            if speed is not None:
                download_speed_map[ip] = speed
            download_passed_map[ip] = ok
            if err:
                download_error_map[ip] = err

    if download_test_enabled:
        def compare_ip(left_ip: str, right_ip: str) -> int:
            left_http = http_passed_map.get(left_ip, False)
            right_http = http_passed_map.get(right_ip, False)
            if left_http != right_http:
                return -1 if left_http else 1
            left_download = download_passed_map.get(left_ip, False)
            right_download = download_passed_map.get(right_ip, False)
            if left_download != right_download:
                return -1 if left_download else 1
            left_latency = latency_map.get(left_ip, float("inf"))
            right_latency = latency_map.get(right_ip, float("inf"))
            left_speed = download_speed_map.get(left_ip, -1.0)
            right_speed = download_speed_map.get(right_ip, -1.0)
            if str(download_ranking_mode or "").strip().lower() == "download_first":
                if left_speed != right_speed:
                    return -1 if left_speed > right_speed else 1
                if left_latency != right_latency:
                    return -1 if left_latency < right_latency else 1
            else:
                if left_latency != right_latency:
                    return -1 if left_latency < right_latency else 1
                if left_speed != right_speed:
                    return -1 if left_speed > right_speed else 1
            return -1 if left_ip < right_ip else (1 if left_ip > right_ip else 0)

        passed.sort(key=cmp_to_key(compare_ip))
    elif tcp_check:
        passed.sort(key=lambda ip: latency_map.get(ip, float("inf")))

    out: List[Dict[str, object]] = []
    for ip in passed[:effective_limit]:
        latency = latency_map.get(ip)
        speed = download_speed_map.get(ip)
        item = {
            "ip": ip,
            "latencyMs": latency if latency is not None else None,
            "httpPassed": http_passed_map.get(ip, False),
            "downloadSpeedMBps": speed if speed is not None else None,
            "downloadPassed": download_passed_map.get(ip, False),
        }
        if download_error_map.get(ip):
            item["downloadError"] = download_error_map[ip]
        out.append(item)
    return out
