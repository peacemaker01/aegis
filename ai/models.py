# ai/models.py
# OpenRouter model registry with costs + use-case notes

MODELS = {
    "claude-sonnet": {
        "id":    "anthropic/claude-sonnet-4-5",
        "label": "Claude Sonnet 4.5",
        "note":  "Best Solidity reasoning, highest accuracy",
        "cost_in":  3.00,   # USD per 1M input tokens
        "cost_out": 15.00,  # USD per 1M output tokens
        "tier":  "premium",
    },
    "deepseek-r1": {
        "id":    "deepseek/deepseek-r1",
        "label": "DeepSeek R1",
        "note":  "Excellent reasoning, very cost-effective",
        "cost_in":  0.55,
        "cost_out": 2.19,
        "tier":  "balanced",
    },
    "gemini-flash": {
        "id":    "google/gemini-flash-2.0",
        "label": "Gemini Flash 2.0",
        "note":  "Fastest response time",
        "cost_in":  0.10,
        "cost_out": 0.40,
        "tier":  "fast",
    },
    "llama-free": {
        "id":    "meta-llama/llama-3.3-70b-instruct:free",
        "label": "Llama 3.3 70B (Free)",
        "note":  "Free tier — rate limited, good for testing",
        "cost_in":  0.00,
        "cost_out": 0.00,
        "tier":  "free",
    },
}

# Default recommended model
DEFAULT_MODEL_KEY = "deepseek-r1"
DEFAULT_MODEL_ID  = MODELS[DEFAULT_MODEL_KEY]["id"]

# Fallback chain: try in order if primary fails
FALLBACK_ORDER = ["claude-sonnet", "deepseek-r1", "gemini-flash", "llama-free"]


def get_model_id(key: str) -> str:
    """Return the OpenRouter model ID for a given short key."""
    if key in MODELS:
        return MODELS[key]["id"]
    # Maybe user passed a raw model ID like "anthropic/claude-..."
    for m in MODELS.values():
        if m["id"] == key:
            return key
    # Return as-is and let OpenRouter validate it
    return key


def list_models() -> list[dict]:
    return [{"key": k, **v} for k, v in MODELS.items()]
