# CPA Worker

This module owns xAI OAuth authorization, CPA credential generation, local hotload copies, and remote CPA uploads.

`cpa_export.py` is copied with `cpa_xai/` into each isolated registration task. The console also imports it directly for existing-account authorization.

Remote CPA credentials are read from `CPA_CLOUD_MANAGEMENT_KEY` when available. Do not store that key in task files.
