# core/utils/state_resolver.py
import json

class StateResolver:
    @staticmethod
    def safe_format(template_str: str, state_dict: dict) -> str:
        if not isinstance(template_str, str): return template_str

        result = template_str
        start_idx = 0
        while True:
            # Find the opening brace
            start = result.find('{', start_idx)
            if start == -1: break
            
            # Skip double braces (which indicate JSON formatting, not variables)
            if start + 1 < len(result) and result[start+1] == '{':
                start_idx = start + 2
                continue

            # Find the closing brace
            end = result.find('}', start)
            if end == -1: break

            var_path = result[start+1:end].strip()

            # Skip obvious JSON blocks or CSS inside braces
            if '"' in var_path or "'" in var_path or ":" in var_path:
                start_idx = end + 1
                continue

            current = state_dict
            valid = True
            
            # Convert array brackets to dots for easy splitting (e.g. item[0] -> item.0)
            clean_path = var_path.replace('[', '.').replace(']', '')
            keys = [k for k in clean_path.split('.') if k]
            
            try:
                for key in keys:
                    if key.isdigit() and isinstance(current, list):
                        current = current[int(key)]
                    else:
                        if isinstance(current, str) and (current.startswith('{') or current.startswith('[')):
                            current = json.loads(current)
                        current = current[key]
            except (KeyError, IndexError, TypeError, Exception):
                valid = False

            # If we found the variable in the state, replace it!
            if valid and current is not None:
                val_str = str(current) if not isinstance(current, (dict, list)) else json.dumps(current)
                result = result[:start] + val_str + result[end+1:]
                start_idx = start + len(val_str)
            else:
                # If variable isn't in state, leave it as {variable} and move on
                start_idx = end + 1

        return result

    @staticmethod
    def resolve_val(val, state_dict: dict, prompt_manager=None):
        if isinstance(val, str): 
            # 1. Regex-Free Dynamic Prompt Fetching {prompt:Name}
            if prompt_manager:
                start_idx = 0
                while True:
                    start = val.find('{prompt:', start_idx)
                    if start == -1: break
                    
                    end = val.find('}', start)
                    if end == -1: break
                    
                    prompt_name = val[start+8:end].strip()
                    fetched = prompt_manager.get_prompt(prompt_name)
                    
                    # Replace the {prompt:...} tag with the actual text
                    val = val[:start] + fetched + val[end+1:]
                    start_idx = start + len(fetched)

            # 2. Variable Formatting
            res = StateResolver.safe_format(val, state_dict)
            
            # 3. Auto-parse JSON strings safely
            if (res.strip().startswith('[') and res.strip().endswith(']')) or \
               (res.strip().startswith('{') and res.strip().endswith('}')):
                try: return json.loads(res)
                except: pass
            return res
            
        elif isinstance(val, list): 
            return [StateResolver.resolve_val(i, state_dict, prompt_manager) for i in val]
        elif isinstance(val, dict): 
            return {k: StateResolver.resolve_val(v, state_dict, prompt_manager) for k, v in val.items()}
            
        return val