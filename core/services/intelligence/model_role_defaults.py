"""Default model recommendations per intelligence role and hardware tier.

Pure data — no logic. These are only *suggestions* surfaced in the model-role
settings UI; the user's actual selections are stored in project metadata by
ModelRoleService and always take precedence. Keeping the tables in a dedicated
data module keeps model-name literals out of service logic (rules.md 16.3).
"""
from __future__ import annotations

from core.models.intelligence_models import ModelRole

ROLE_RECOMMENDATIONS = {
    ModelRole.MAIN_ANSWER.value: {
        "choices": ["llama3.2:8b", "qwen2.5:7b", "gemma2:9b"],
        "guide": "Choose the best reasoning/chat model your hardware can run comfortably. Larger models improve synthesis and writing quality.",
    },
    ModelRole.FAST_WORKER.value: {
        "choices": ["llama3.2:3b", "qwen2.5:3b", "gemma4:e2b"],
        "guide": "Choose a small fast instruct model for JSON, labels, short summaries, and background helper tasks.",
    },
    ModelRole.EMBEDDING.value: {
        "choices": ["nomic-embed-text", "mxbai-embed-large", "nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf"],
        "guide": "Use an embedding model, not a chat model. Changing this requires rebuilding embeddings.",
    },
    ModelRole.RERANKER.value: {
        "choices": ["qwen2.5:3b", "llama3.2:3b", "gemma4:e2b"],
        "guide": "Optional. Use a fast worker model unless you have a dedicated reranker backend.",
    },
    ModelRole.VISION.value: {
        "choices": ["llava:7b", "llava:13b", "bakllava:latest"],
        "guide": "Choose a model tagged for vision if you analyze figures, screenshots, or scanned visual content.",
    },
    ModelRole.TRANSCRIPTION.value: {
        "choices": ["small", "medium", "large-v3"],
        "guide": "Whisper/faster-whisper model size. Larger improves transcription accuracy but costs more CPU/GPU time.",
    },
    ModelRole.EXTRACTION_REPAIR.value: {
        "choices": ["llama3.2:3b", "qwen2.5:3b", "mistral:7b"],
        "guide": "Optional. Use a reliable JSON/instruction-following model for repairing ambiguous deterministic extraction.",
    },
}

LOW_HARDWARE_RECOMMENDATIONS = {
    ModelRole.MAIN_ANSWER.value: ["gemma4:e2b", "qwen2.5:3b", "llama3.2:3b"],
    ModelRole.FAST_WORKER.value: ["qwen2.5:0.5b", "lfm2:1.2b", "llama3.2:1b"],
    ModelRole.EMBEDDING.value: ["nomic-embed-text", "mxbai-embed-large", "nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf"],
    ModelRole.RERANKER.value: ["qwen2.5:0.5b", "lfm2:1.2b", "gemma4:e2b"],
    ModelRole.VISION.value: ["llava:7b", "bakllava:latest", "llava:13b"],
    ModelRole.TRANSCRIPTION.value: ["tiny", "base", "small"],
    ModelRole.EXTRACTION_REPAIR.value: ["qwen2.5:0.5b", "lfm2:1.2b", "gemma4:e2b"],
}

BALANCED_HARDWARE_RECOMMENDATIONS = {
    ModelRole.MAIN_ANSWER.value: ["gemma4:e2b", "llama3.2:3b", "qwen2.5:3b"],
    ModelRole.FAST_WORKER.value: ["llama3.2:1b", "qwen2.5:0.5b", "lfm2:1.2b"],
    ModelRole.EMBEDDING.value: LOW_HARDWARE_RECOMMENDATIONS[ModelRole.EMBEDDING.value],
    ModelRole.RERANKER.value: ["llama3.2:1b", "qwen2.5:0.5b", "lfm2:1.2b"],
    ModelRole.VISION.value: LOW_HARDWARE_RECOMMENDATIONS[ModelRole.VISION.value],
    ModelRole.TRANSCRIPTION.value: ["base", "small", "medium"],
    ModelRole.EXTRACTION_REPAIR.value: ["llama3.2:1b", "qwen2.5:3b", "gemma4:e2b"],
}

HIGH_HARDWARE_RECOMMENDATIONS = {
    ModelRole.MAIN_ANSWER.value: ["llama3.2:8b", "qwen2.5:7b", "gemma2:9b"],
    ModelRole.FAST_WORKER.value: ["llama3.2:3b", "qwen2.5:3b", "gemma4:e2b"],
    ModelRole.EMBEDDING.value: LOW_HARDWARE_RECOMMENDATIONS[ModelRole.EMBEDDING.value],
    ModelRole.RERANKER.value: ["qwen2.5:3b", "llama3.2:3b", "gemma4:e2b"],
    ModelRole.VISION.value: ["llava:7b", "llava:13b", "bakllava:latest"],
    ModelRole.TRANSCRIPTION.value: ["small", "medium", "large-v3"],
    ModelRole.EXTRACTION_REPAIR.value: ["qwen2.5:3b", "llama3.2:3b", "mistral:7b"],
}
