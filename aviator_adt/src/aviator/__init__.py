import os

# Set GOOGLE_GENAI_USE_VERTEXAI to true by default, but allow override
if "GOOGLE_GENAI_USE_VERTEXAI" not in os.environ:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
