import json


MASTER_CHUNK_SENTINEL = 999999
PAGES_PER_CHUNK = 4


def parse_db_row(row):
    """Extract (chunk_index, json_data) from any SQLite row format."""
    c_idx, j_data = 0, "{}"
    if hasattr(row, 'keys'):
        c_idx = row['chunk_index']
        j_data = row['json_data']
    elif isinstance(row, dict):
        c_idx = row.get('chunk_index', 0)
        j_data = row.get('json_data', '{}')
    else:
        for val in row:
            if isinstance(val, int) and val < 1000:
                c_idx = val
        for val in reversed(row):
            if isinstance(val, str) and (val.strip().startswith('{') or val.strip().startswith('[')):
                j_data = val
                break
    return c_idx, j_data


def mathematical_merge(analyses):
    """Deterministically merge JSON analysis chunks, deduplicating identical entries."""
    if not analyses:
        return "{}"
    master_dict = {}
    for row in analyses:
        c_idx, j_data = parse_db_row(row)
        try:
            from core.utils.json_utils import extract_and_heal_json
            success, parsed = extract_and_heal_json(j_data)
            if not success:
                continue
            items_to_process = parsed if isinstance(parsed, list) else [parsed]
            for obj in items_to_process:
                if not isinstance(obj, dict):
                    continue
                for key, val in obj.items():
                    if key not in master_dict:
                        master_dict[key] = []
                    vals_to_add = val if isinstance(val, list) else [val]
                    for v in vals_to_add:
                        if isinstance(v, dict):
                            v_str = json.dumps(v, sort_keys=True)
                            if not any(isinstance(e, dict) and json.dumps(e, sort_keys=True) == v_str for e in master_dict[key]):
                                master_dict[key].append(v)
                        else:
                            if v not in master_dict[key]:
                                master_dict[key].append(v)
        except Exception as e:
            print(f"[Merge Error] {e}")
    return json.dumps(master_dict)
