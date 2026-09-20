"""
ROSE System Information Module
Provides system information and time/date awareness as controlled tools.
No arbitrary code execution — read-only system queries.
"""
import os
import platform
import socket
import datetime
import psutil


def get_current_time() -> dict:
    """Return current system date and time."""
    now = datetime.datetime.now()
    return {
        "time": now.strftime("%I:%M %p"),
        "time_24h": now.strftime("%H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "date_friendly": now.strftime("%A, %B %d, %Y"),
        "day": now.strftime("%A"),
        "year": now.year,
        "month": now.strftime("%B"),
        "timestamp": now.isoformat(),
    }


def get_system_info() -> dict:
    """Return basic system information (read-only, safe to expose)."""
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version,
        "machine": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "hostname": socket.gethostname(),
        "cpu_count_physical": psutil.cpu_count(logical=False) or 1,
        "cpu_count_logical": psutil.cpu_count(logical=True) or 1,
        "cpu_usage_percent": psutil.cpu_percent(interval=0.1),
    }

    # Memory
    mem = psutil.virtual_memory()
    info["ram_total_gb"] = round(mem.total / (1024**3), 1)
    info["ram_used_gb"] = round(mem.used / (1024**3), 1)
    info["ram_available_gb"] = round(mem.available / (1024**3), 1)
    info["ram_usage_percent"] = mem.percent

    # Disk
    try:
        disk = psutil.disk_usage("/")
        info["disk_total_gb"] = round(disk.total / (1024**3), 1)
        info["disk_used_gb"] = round(disk.used / (1024**3), 1)
        info["disk_free_gb"] = round(disk.free / (1024**3), 1)
        info["disk_usage_percent"] = disk.percent
    except Exception:
        pass

    # Battery (if available)
    try:
        battery = psutil.sensors_battery()
        if battery:
            info["battery_percent"] = battery.percent
            info["battery_plugged"] = battery.power_plugged
            info["battery_charging"] = battery.power_plugged and battery.percent < 100
    except Exception:
        pass

    # Network
    try:
        net_ifs = psutil.net_if_addrs()
        for iface, addrs in net_ifs.items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    info["ip_address"] = addr.address
                    info["network_interface"] = iface
                    break
            if "ip_address" in info:
                break
    except Exception:
        pass

    # Uptime
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = datetime.datetime.now().timestamp() - boot_time
        uptime_hours = int(uptime_seconds // 3600)
        uptime_minutes = int((uptime_seconds % 3600) // 60)
        info["uptime"] = f"{uptime_hours}h {uptime_minutes}m"
    except Exception:
        pass

    return info


def get_cpu_info() -> str:
    """Return CPU usage as a friendly string."""
    usage = psutil.cpu_percent(interval=0.1)
    return f"CPU usage is {usage}%."


def get_ram_info() -> str:
    """Return RAM usage as a friendly string."""
    mem = psutil.virtual_memory()
    used_gb = round(mem.used / (1024**3), 1)
    total_gb = round(mem.total / (1024**3), 1)
    return f"RAM usage is {used_gb} GB out of {total_gb} GB ({mem.percent}%)."


def get_disk_info() -> str:
    """Return disk usage as a friendly string."""
    try:
        disk = psutil.disk_usage("/")
        used_gb = round(disk.used / (1024**3), 1)
        total_gb = round(disk.total / (1024**3), 1)
        free_gb = round(disk.free / (1024**3), 1)
        return f"Disk usage is {used_gb} GB out of {total_gb} GB. Free space: {free_gb} GB."
    except Exception:
        return "Could not retrieve disk information."


def get_battery_info() -> str:
    """Return battery status as a friendly string."""
    try:
        battery = psutil.sensors_battery()
        if battery:
            status = "charging" if battery.power_plugged else "on battery"
            return f"Battery is at {battery.percent}% ({status})."
        return "No battery detected (desktop computer)."
    except Exception:
        return "Could not retrieve battery information."


def get_network_info() -> str:
    """Return network status as a friendly string."""
    try:
        hostname = socket.gethostname()
        net_ifs = psutil.net_if_addrs()
        ip = None
        for iface, addrs in net_ifs.items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ip = addr.address
                    break
            if ip:
                break
        if ip:
            return f"Hostname: {hostname}. IP address: {ip}."
        return f"Hostname: {hostname}. No active network interface found."
    except Exception:
        return "Could not retrieve network information."


# ---- Voice command patterns for system info ----
TIME_PATTERNS = [
    r".*\b(what.*time|current\s*time|time\s*is\s*it|tell\s*me\s*the\s*time)\b.*",
    r".*\b(what.*date|today.*date|date\s*is\s*it|what\s*day)\b.*",
]

SYSTEM_INFO_PATTERNS = [
    r".*\b(system\s*info|computer\s*info|pc\s*info|machine\s*info)\b.*",
    r".*\b(cpu\s*(usage|info)|how.*cpu)\b.*",
    r".*\b(ram\s*(usage|info)|memory\s*(usage|info)|how.*ram)\b.*",
    r".*\b(disk\s*(usage|space|info)|storage\s*(usage|space|info)|how.*storage)\b.*",
    r".*\b(battery\s*(info|status|level)|is.*charging|how.*battery)\b.*",
    r".*\b(network\s*(info|status)|ip\s*address|wifi\s*(status|connected)|internet\s*(status|connected))\b.*",
]


def check_time_query(text: str) -> bool:
    """Check if the user is asking about time/date."""
    import re
    lower = text.lower()
    return any(re.match(pat, lower) for pat in TIME_PATTERNS)


def check_system_info_query(text: str) -> bool:
    """Check if the user is asking about system information."""
    import re
    lower = text.lower()
    return any(re.match(pat, lower) for pat in SYSTEM_INFO_PATTERNS)


def handle_system_query(text: str) -> str:
    """
    Handle system information queries. Returns a friendly response string.
    Returns None if not a system query.
    """
    lower = text.lower()

    # Time/Date queries
    if check_time_query(text):
        time_info = get_current_time()
        if any(w in lower for w in ["time", "what time"]):
            return f"It's {time_info['time']}."
        if any(w in lower for w in ["date", "day", "today"]):
            return f"Today is {time_info['date_friendly']}."
        return f"It's {time_info['time']} on {time_info['date_friendly']}."

    # System info queries
    if check_system_info_query(text):
        if any(w in lower for w in ["cpu"]):
            return get_cpu_info()
        if any(w in lower for w in ["ram", "memory"]):
            return get_ram_info()
        if any(w in lower for w in ["disk", "storage"]):
            return get_disk_info()
        if any(w in lower for w in ["battery"]):
            return get_battery_info()
        if any(w in lower for w in ["network", "wifi", "ip", "internet"]):
            return get_network_info()
        # General system info
        info = get_system_info()
        return (f"You're running {info['os']} on a {info['machine']}. "
                f"CPU usage is {info['cpu_usage_percent']}%, "
                f"RAM is {info['ram_usage_percent']}% used, "
                f"and disk is {info['disk_usage_percent']}% used.")

    return None
