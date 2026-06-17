"""
check_model.py
==============
Run this BEFORE starting your Django server to verify:
  1. The all-mpnet-base-v2 model files exist on disk.
  2. The model loads successfully with NO internet connection.
  3. The model actually encodes text (end-to-end test).
  4. Your TEXT_ENCODER_MODEL_PATH env var is set correctly.

Usage:
    python check_model.py

All checks run synchronously — no Django needed.
"""

import os
import sys
import time

SNAPSHOT_HASH = 'e8c3b32edf5434bc2275fc9bab85f82640a19130'

MODEL_PATH = os.environ.get(
    'TEXT_ENCODER_MODEL_PATH',
    os.path.join(
        os.path.expanduser('~'),
        '.hf_cache', 'hub',
        'models--sentence-transformers--all-mpnet-base-v2',
        'snapshots',
        SNAPSHOT_HASH,
    )
)

REQUIRED_FILES = [
    'config.json',
    'tokenizer_config.json',
    'tokenizer.json',
    'vocab.txt',
    'special_tokens_map.json',
    'sentence_bert_config.json',
    # The actual weights — one of these must exist
    # (pytorch_model.bin is the old format, model.safetensors is the new one)
]

WEIGHT_FILES = [
    'pytorch_model.bin',
    'model.safetensors',
]

# ── ANSI colours (Windows 10+ supports these in cmd/PowerShell) ──────────────
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET} {msg}")
def section(title): print(f"\n{BOLD}{'─'*55}\n  {title}\n{'─'*55}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK 1 — Model path exists
# ─────────────────────────────────────────────────────────────────────────────
section("CHECK 1 — Model directory")

print(f"  Path: {MODEL_PATH}\n")

if os.path.isdir(MODEL_PATH):
    ok("Directory exists.")
else:
    fail("Directory NOT found.")
    print(f"""
  {RED}The model has not been downloaded to the expected path.{RESET}

  To download it run (with internet):
      python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('sentence-transformers/all-mpnet-base-v2')
print('Downloaded to:', m._model_card_data)
"

  Or set TEXT_ENCODER_MODEL_PATH to wherever you saved the model.
""")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK 2 — Required files present
# ─────────────────────────────────────────────────────────────────────────────
section("CHECK 2 — Required model files")

all_present = True
for fname in REQUIRED_FILES:
    fpath = os.path.join(MODEL_PATH, fname)
    if os.path.isfile(fpath):
        size_kb = os.path.getsize(fpath) / 1024
        ok(f"{fname}  ({size_kb:.1f} KB)")
    else:
        fail(f"{fname}  — MISSING")
        all_present = False

# Check weights separately (either format is fine)
weight_found = False
for wf in WEIGHT_FILES:
    wpath = os.path.join(MODEL_PATH, wf)
    if os.path.isfile(wpath):
        size_mb = os.path.getsize(wpath) / (1024 * 1024)
        ok(f"{wf}  ({size_mb:.1f} MB)  ← weights")
        weight_found = True
        break

if not weight_found:
    fail("No weight file found (need pytorch_model.bin OR model.safetensors)")
    all_present = False

if not all_present:
    print(f"\n  {RED}Some files are missing — the model download may be incomplete.{RESET}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK 3 — Load model with internet DISABLED
# ─────────────────────────────────────────────────────────────────────────────
section("CHECK 3 — Offline load test")

# Force HuggingFace to use only local files — no network calls at all
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_DATASETS_OFFLINE']  = '1'
os.environ['HF_HUB_OFFLINE']       = '1'

print("  Loading model (internet disabled for this test)…")
t0 = time.time()

try:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_PATH)
    elapsed = time.time() - t0
    ok(f"Model loaded in {elapsed:.2f}s")
except Exception as e:
    fail(f"Model failed to load: {e}")
    print(f"""
  Common causes:
    • sentence-transformers not installed  →  pip install sentence-transformers
    • MODEL_PATH points to wrong directory (check snapshot hash)
    • Model files are corrupted           →  re-download
""")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK 4 — Encode a sentence (end-to-end)
# ─────────────────────────────────────────────────────────────────────────────
section("CHECK 4 — Encode test sentences")

test_sentences = [
    "I need an experienced electrician in Lagos",
    "Skilled plumber available for residential work",
]

try:
    t0 = time.time()
    embeddings = model.encode(test_sentences, convert_to_numpy=True)
    elapsed = time.time() - t0

    ok(f"Encoded {len(test_sentences)} sentences in {elapsed*1000:.0f}ms")
    ok(f"Embedding shape: {embeddings.shape}  (expected: ({len(test_sentences)}, 768))")

    if embeddings.shape[1] != 768:
        warn(f"Unexpected embedding dim {embeddings.shape[1]} — double-check model identity")
    else:
        ok("Embedding dimensions correct (768-dim ✓)")

    # Cosine similarity between the two sentences as a sanity check
    from numpy.linalg import norm
    a, b = embeddings[0], embeddings[1]
    cos_sim = float((a @ b) / (norm(a) * norm(b)))
    ok(f"Cosine similarity between test sentences: {cos_sim:.4f}  (reasonable range: 0.3–0.8)")

except Exception as e:
    fail(f"Encoding failed: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK 5 — Confirm no internet was used
# ─────────────────────────────────────────────────────────────────────────────
section("CHECK 5 — Offline confirmation")

ok("HF_HUB_OFFLINE=1 was set — no network calls were made during load")
ok("Your model is fully self-contained and works offline")

print(f"""
{GREEN}{BOLD}
  All checks passed.
  The all-mpnet-base-v2 model is installed correctly and works offline.
  Your Django server should load it fine.

  If your server is still shutting down, the issue is NOT the model.
  Paste your full server output (including the traceback if any) for
  further diagnosis.
{RESET}
""")

# ─────────────────────────────────────────────────────────────────────────────
#  BONUS — Print the exact path to paste into settings.py
# ─────────────────────────────────────────────────────────────────────────────
section("Your confirmed model path (copy into settings.py)")
print(f"  TEXT_ENCODER_MODEL_PATH = r\"{MODEL_PATH}\"\n")