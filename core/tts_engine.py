import subprocess
import os
import shutil
import sys

from core.text_utils import sanitize_extracted_text


def _resolve_piper_command():
    """Prefer interpreter-local Piper to avoid mixed venv/.venv PATH issues."""
    candidates = []

    exe_dir = os.path.dirname(sys.executable)
    candidates.extend([
        os.path.join(exe_dir, "piper.exe"),
        os.path.join(exe_dir, "piper"),
    ])

    cwd = os.getcwd()
    candidates.extend([
        os.path.join(cwd, ".venv", "Scripts", "piper.exe"),
        os.path.join(cwd, ".venv", "bin", "piper"),
        os.path.join(cwd, "venv", "Scripts", "piper.exe"),
        os.path.join(cwd, "venv", "bin", "piper"),
    ])

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("piper")

def generate_audio(text, output_filepath, voice_file="voice1.onnx", speed=1.0, progress_callback=None):
    try:
        if progress_callback:
            progress_callback("Initializing Piper TTS engine...")

        piper_cmd = _resolve_piper_command()
        if not piper_cmd:
            return "CRITICAL ERROR: 'piper' not found.\nPlease run: pip install piper-tts"

        # Setup absolute paths
        output_filepath = os.path.abspath(output_filepath)
        
        model_path = os.path.abspath(os.path.join("models", voice_file))
        
        if not os.path.exists(model_path):
            return f"PIPER MODEL MISSING: Please ensure '{voice_file}' and its .json file are inside the 'models/' folder."

        clean_text = sanitize_extracted_text(text, collapse_whitespace=True)
        if not clean_text:
            return "No valid text remained after cleaning the extracted PDF text."

        # Keep Piper line handling predictable and sanitize each line again defensively.
        cleaned_lines = [sanitize_extracted_text(line, collapse_whitespace=True) for line in clean_text.splitlines()]
        clean_text = "\n".join([line for line in cleaned_lines if line])
        if clean_text:
            clean_text += "\n"
        else:
            return "No valid text remained after cleaning the extracted PDF text."

        if progress_callback:
            progress_callback(f"Generating audio...")

        command = [piper_cmd, "--model", model_path, "--output_file", output_filepath]

        # Adjust piper speed (Piper uses length_scale, where lower is faster. 1.0 is normal)
        if speed != 1.0:
            piper_speed = 1.0 / speed
            command.extend(["--length_scale", str(piper_speed)])

        run_env = os.environ.copy()
        # Force UTF-8 stdio in Piper process to prevent surrogateescape artifacts on Windows code pages.
        run_env["PYTHONIOENCODING"] = "utf-8"
        run_env["PYTHONUTF8"] = "1"

        result = subprocess.run(
            command,
            input=clean_text.encode("utf-8"),
            capture_output=True,
            check=False,
            env=run_env,
        )

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            return f"System Error from Piper ({os.path.basename(piper_cmd)}):\n{stderr_text}"

        if progress_callback:
            progress_callback("Done!")

        return True

    except PermissionError as e:
        return f"Permission Error: {str(e)}\n(Cannot save audio file in this folder.)"
    except Exception as e:
        return f"Unexpected Error: {type(e).__name__} - {str(e)}"