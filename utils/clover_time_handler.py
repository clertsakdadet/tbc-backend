from datetime import datetime, time
import zoneinfo

def epoch_to_readable(epoch_ms):
    # Convert milliseconds to seconds and localize
    utc_dt = datetime.fromtimestamp(epoch_ms / 1000, tz=zoneinfo.ZoneInfo('UTC'))
    pst_dt = utc_dt.astimezone(zoneinfo.ZoneInfo('America/Los_Angeles'))
    return {
        'utc': utc_dt.isoformat(),
        'pst': pst_dt.isoformat()
    }

def readable_to_epoch(date_str, position, tz_str='America/Los_Angeles'):
    tz = zoneinfo.ZoneInfo(tz_str)

    # Accept both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS±HH:MM" formats
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            dt = dt.replace(tzinfo=tz)
    except Exception as e:
        raise ValueError(f"Invalid date string: {date_str}") from e

    if position == 'start':
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif position == 'end':
        dt = dt.replace(hour=23, minute=59, second=0, microsecond=0)
    elif position == 'exact':
        # If time is already present in date_str, keep it; else set to noon
        if "T" not in date_str:
            dt = dt.replace(hour=12, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("position must be 'start', 'end' or 'exact'")

    return int(dt.timestamp() * 1000)

# Example:
# epoch_ms = 1722041400000  # sample Clover epoch in ms
# readable_times = epoch_to_readable(epoch_ms)
# print("UTC:", readable_times['utc'])
# print("PST:", readable_times['pst'])

# print("\nReadable to Epoch:")
# print("Start of 2025-06-21 (PST):", readable_to_epoch("2025-06-21", "start"))
# print("End of 2025-07-19 (PST):", readable_to_epoch("2025-07-19", "end"))
# print("Exact time:", readable_to_epoch("2025-07-15T17:00:00-07:00", "exact"))