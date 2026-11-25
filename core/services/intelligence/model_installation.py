"""Cancellable package installation for catalog-driven model installers."""
import subprocess
import tempfile
import time
import sys
import importlib.util
import shutil

from core.services.document.ocr_engine_setup import install_command, manual_install_hint, tesseract_path

INSTALL_POLL_SECONDS = 0.1
TERMINATE_TIMEOUT_SECONDS = 3


def run_package_command(cmd, log, cancelled=None):
    cancelled = cancelled or (lambda: False)
    if cancelled():
        return {"ok": False, "error": "Cancelled"}
    log("$ " + " ".join(cmd))
    with tempfile.TemporaryFile(mode="w+t") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                if cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    return {"ok": False, "error": "Cancelled"}
                time.sleep(INSTALL_POLL_SECONDS)
            output.seek(0)
            message = output.read()
            if message:
                log(message.strip())
            return {"ok": proc.returncode == 0, "error": message.strip() if proc.returncode else ""}
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def ensure_runtimes(spec, log, cancelled):
    modules = [*spec.runtime_modules, *getattr(spec, "install_modules", [])]
    missing = [module for module in modules if importlib.util.find_spec(module) is None]
    if not missing:
        return {"ok": True}
    return run_package_command([sys.executable, "-m", "pip", "install", *missing], log, cancelled)


def system_status(spec):
    """``ready`` when the OS program is on PATH (Tesseract today)."""
    if spec.system_binary == "tesseract":
        return "ready" if tesseract_path() else "missing"
    return "ready" if shutil.which(spec.system_binary) else "missing"


def system_installable(spec):
    if spec.system_binary != "tesseract":
        return False
    return install_command() is not None


def install_system_dependency(spec, log, cancelled):
    cmd = install_command() if spec.system_binary == "tesseract" else None
    if not cmd:
        return {"ok": False, "error": "No supported package manager was found. " + manual_install_hint()}
    log("Your system will ask for permission to install software.")
    result = run_package_command(cmd, log, cancelled)
    if not result.get("ok"):
        error = result.get("error") or ""
        # pkexec exits 126/127 when the password prompt is dismissed or denied.
        if "dismissed" in error.lower() or "not authorized" in error.lower() or not error:
            error = "Installation was not authorised. " + manual_install_hint()
        return {"ok": False, "error": error}
    if system_status(spec) != "ready":
        return {"ok": False, "error": "The installer finished but the program was not found. "
                + manual_install_hint()}
    return {"ok": True}


def perform_operation(service, dep_id, action, source_dir, **kwargs):
    if action == "install":
        return service.install(dep_id, **kwargs)
    if not service._install_lock.acquire(blocking=False):
        return {"ok": False, "error": "Another model operation is in progress."}
    try:
        if action == "import":
            result = service.import_model_folder(dep_id, source_dir)
        elif action == "validate":
            result = service.validate(dep_id)
        else:
            raise ValueError("Unknown model operation.")
        service._operation_errors[dep_id] = str(result.get("error") or "")
        return result
    finally:
        service._install_lock.release()
