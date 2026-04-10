import subprocess
import os
import shutil

def generate_audio(text, output_filepath, voice_file="voice1.onnx", speed=1.0, progress_callback=None):
    try:
        if progress_callback:
            progress_callback("Initializing Piper TTS engine...")

        if not shutil.which("piper"):
            return "CRITICAL ERROR: 'piper' not found.\nPlease run: pip install piper-tts"

        # Setup absolute paths
        temp_text_file = os.path.abspath("temp_tts_input.txt")
        output_filepath = os.path.abspath(output_filepath)
        
        model_path = os.path.abspath(os.path.join("models", voice_file))
        
        if not os.path.exists(model_path):
            return f"PIPER MODEL MISSING: Please ensure '{voice_file}' and its .json file are inside the 'models/' folder."

        # Write text to temp file for Piper
        with open(temp_text_file, "w", encoding="utf-8") as f:
            f.write(text)

        if progress_callback:
            progress_callback(f"Generating audio...")

        # Run Piper (outputs directly to our chosen WAV filepath)
        with open(temp_text_file, "r", encoding="utf-8") as f_in:
            command = ["piper", "--model", model_path, "--output_file", output_filepath]
            
            # Adjust piper speed (Piper uses length_scale, where lower is faster. 1.0 is normal)
            if speed != 1.0:
                piper_speed = 1.0 / speed
                command.extend(["--length_scale", str(piper_speed)])
                
            result = subprocess.run(command, stdin=f_in, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            if os.path.exists(temp_text_file): os.remove(temp_text_file)
            return f"System Error from Piper:\n{result.stderr.strip()}"

        # Clean up temporary text file
        if os.path.exists(temp_text_file):
            os.remove(temp_text_file)

        if progress_callback:
            progress_callback("Done!")

        return True

    except PermissionError as e:
        return f"Permission Error: {str(e)}\n(Cannot save audio file in this folder.)"
    except Exception as e:
        return f"Unexpected Error: {type(e).__name__} - {str(e)}"