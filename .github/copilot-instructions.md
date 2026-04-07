# Copilot Instructions for PDF Workspace

- Project entrypoint is `main.py`. It builds a PyQt6 application, sets a global exception handler, applies theme settings, and opens the last-used `.pdfproj`.
- `gui/` contains the UI layer. `gui/main_window.py` orchestrates the app, menus, project actions, and tool panels.
- `core/` contains the app logic. `core/project_manager.py` manages project persistence in a `.pdfproj` SQLite file and caches open PDF docs with `fitz`.
- `core/llm_manager.py` is the AI integration layer. It expects a local Ollama server at `http://localhost:11434` and uses `chromadb.PersistentClient` in a project-specific `_chroma_db` folder.
- `gui/tabs/llm_tab.py` is the main AI UI flow. It uses `QThread` workers for indexing and chat, and streams model output via signals.

- Important persistence rule: projects are saved in two parts:
  - `project.pdfproj` SQLite database for metadata/workspace nodes/edges
  - `project.pdfproj_chroma_db/` folder for ChromaDB embeddings
  Any project copy/save-as logic must preserve both.

- Long-running operations must stay off the Qt main thread. Existing pattern:
  - `IndexWorker` for `llm_manager.index_documents`
  - `ChatWorker` for `llm_manager.query`
  - Signals emit progress and update UI status labels.

- Use `self.main_window.project_manager` from tabs to read project PDF list, active project path, and open docs.
- `project_manager.save_workspace_data()` uses bulk SQLite transactions; `add_pdf()` also updates SQLite immediately.
- `local LLM` behavior is not remote API-based: code calls `ollama serve`, `POST /api/embed`, `POST /api/generate`, and `POST /api/pull`.

- To run the app locally:
  - `source venv/bin/activate`
  - `pip install -r requirements.txt`
  - `python main.py`
- To run tests: `python run_tests.py`
- For stress-mode tests: `python run_tests.py --stress --iterations 1000 --concurrency 20`

- Avoid generic changes to project format. If updating `.pdfproj` schema, also update `core/project_manager.py` and any project-load/save UI flows.
- Prefer using existing UI update methods like `LLMTab.refresh_project_ui()` and `MainWindow._refresh_pdf_dropdown()` rather than duplicating state sync logic.
