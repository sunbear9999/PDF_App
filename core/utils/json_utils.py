# core/utils/json_utils.py
import json
import re

def extract_and_heal_json(raw_text: str) -> tuple[bool, dict | list | str]:
    """
    Attempts to extract and parse JSON from a raw LLM output string.
    Features robust mathematical stack-based auto-repair for truncated AI outputs.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return False, "The AI returned an empty or invalid string."

    # 1. Isolate the JSON block
    start_dict = raw_text.find('{')
    start_list = raw_text.find('[')
    
    if start_dict == -1 and start_list == -1:
        return False, raw_text.strip()
        
    is_list = start_list != -1 and (start_dict == -1 or start_list < start_dict)
    start_idx = start_list if is_list else start_dict
    end_idx = raw_text.rfind(']') if is_list else raw_text.rfind('}')
    
    if end_idx == -1 or end_idx < start_idx:
        clean_str = raw_text[start_idx:] # Cut off at the start, let the healer fix the end
    else:
        clean_str = raw_text[start_idx:end_idx+1]

    # 2. Attempt Standard Parse
    try:
        return True, json.loads(clean_str, strict=False)
    except json.JSONDecodeError:
        pass

    # 3. Stack-Based Auto-Repair (Imported from text_utils)
    print("⚠️ [JSON Utils] Syntax error or cutoff detected! Engaging Stack-Based Auto-Repair...")
    clean_str = clean_str.rstrip(', \n')
    
    # Close unclosed string values
    if clean_str.count('"') % 2 != 0:
        clean_str += '"'
        
    # If it cut off inside a key/value pair, close the object
    if not clean_str.endswith('}') and not clean_str.endswith(']'):
        clean_str += '}'
        
    # Balance arrays and objects mathematically
    while clean_str.count('[') > clean_str.count(']'):
        clean_str += ']'
    while clean_str.count('{') > clean_str.count('}'):
        clean_str += '}'
        
    try:
        return True, json.loads(clean_str, strict=False)
    except json.JSONDecodeError as e:
        return False, f"Failed to parse AI JSON even after repair: {e}"

def extract_json_from_tags(raw_text: str, tag_name: str) -> tuple[bool, dict | list | str]:
    """
    Extracts JSON wrapped in specific XML-style tags, ignoring all outside text.
    """
    start_tag = f"<{tag_name}>"
    end_tag = f"</{tag_name}>"
    
    start_idx = raw_text.find(start_tag)
    end_idx = raw_text.find(end_tag)
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        content = raw_text[start_idx + len(start_tag):end_idx]
        return extract_and_heal_json(content)
        
    return False, raw_text