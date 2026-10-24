Dialogue Lab v1.0.1 — Groq HTTP 403 / Cloudflare 1010 fix

UPLOAD ONLY THESE TWO FILES into the existing repository, preserving folders:
  scripts/update.py
  .github/workflows/conversation.yml

DO NOT replace data/state.json, data/transcript.json, or data/outcomes.json.

After committing, open Actions > Continue the dialogue > Run workflow.
The next turn should resume from the last saved speaker.
If another error appears, check Actions > talk > Run next batch.

This update uses the official Groq Python API client rather than bare urllib for Groq.
