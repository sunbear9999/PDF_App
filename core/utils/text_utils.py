# core/utils/text_utils.py
import re
import json

def extract_and_heal_json(raw_text: str) -> tuple[bool, dict | str]:
    """
    Extracts JSON from an LLM response and attempts to repair cut-off syntax.
    Returns (Success_Boolean, Parsed_Dict_or_Error_Message).
    """
    if not raw_text.strip():
        return False, "The AI returned a completely empty string."

    match = re.search(r'\[.*\]|\{.*\}', raw_text, re.DOTALL)
    if not match: 
        return False, "Could not locate JSON brackets in AI response."

    raw_json = match.group(0).strip()

    try:
        parsed_data = json.loads(raw_json)
        return True, parsed_data
    except json.JSONDecodeError:
        print("⚠️ [Text Utils] JSON cut off! Engaging Stack-Based Auto-Repair...")
        raw_json = raw_json.rstrip(', \n')
        
        # 1. Close unclosed string values
        if raw_json.count('"') % 2 != 0:
            raw_json += '"'
            
        # 2. If it cut off inside a key/value pair, close the object
        if not raw_json.endswith('}') and not raw_json.endswith(']'):
            raw_json += '}'
            
        # 3. Balance arrays and objects mathematically
        while raw_json.count('[') > raw_json.count(']'):
            raw_json += ']'
        while raw_json.count('{') > raw_json.count('}'):
            raw_json += '}'
            
        try:
            parsed_data = json.loads(raw_json)
            return True, parsed_data
        except json.JSONDecodeError as e:
            return False, f"Failed to parse AI graph JSON even after repair: {e}"