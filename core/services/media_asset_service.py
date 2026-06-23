from __future__ import annotations


class MediaAssetService:
    def __init__(self, project_manager):
        self.project_manager = project_manager

    def save(self, data: bytes, mime_type: str = "application/octet-stream", metadata=None) -> str:
        return self.project_manager.save_media_asset(data, mime_type, metadata or {})

    def load(self, asset_id: str):
        return self.project_manager.get_media_asset(asset_id)
