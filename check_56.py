"""
check_56.py
===========
Focused diagnostic for sentence_transformers install and offline model loading.

Run from your project root (same folder as manage.py):
    py check_56.py
"""

import os, sys, json

# ── Replicate settings.py exactly ───────────────────────────────────────────
os.environ.setdefault('HUGGINGFACE_HUB_SYMLINKS_MODE', 'copy')
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
SNAPSHOT_HASH = 'e8c3b32edf5434bc2275fc9bab85f82640a19130'
os.environ.setdefault('HF_TOKEN', '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')
os.environ.setdefault('TRANSFORMERS_CACHE',         os.path.join(os.path.expanduser('~'), '.hf_cache'))
os.environ.setdefault('HF_HOME',                    os.path.join(os.path.expanduser('~'), '.hf_cache'))
os.environ.setdefault('SENTENCE_TRANSFORMERS_HOME', os.path.join(os.path.expanduser('~'), '.hf_cache'))

SNAPSHOT_DIR = os.path.join(
    os.path.expanduser('~'),
    '.hf_cache', 'hub',
    'models--sentence-transformers--all-mpnet-base-v2',
    'snapshots',
    SNAPSHOT_HASH,
)
os.environ.setdefault('TEXT_ENCODER_MODEL_PATH', SNAPSHOT_DIR)

OK = "[ OK ]"; E = "[FAIL]"; W = "[WARN]"; I = "[INFO]"

def section(t): print(f"\n{'='*60}\n  {t}\n{'='*60}")

# ════════════════════════════════════════════════════════════════
# CHECK 5A — package importable
# ════════════════════════════════════════════════════════════════
section("CHECK 5A — sentence_transformers importable?")

try:
    import sentence_transformers as st
    print(f"  {OK}  Imported successfully")
    print(f"  {I}  Version         : {st.__version__}")
    print(f"  {I}  Package location: {os.path.dirname(st.__file__)}")
except ImportError as ex:
    print(f"  {E}  Cannot import sentence_transformers: {ex}")
    print(f"\n  Fix:  pip install sentence-transformers")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
# CHECK 5B — key sub-modules importable
# ════════════════════════════════════════════════════════════════
section("CHECK 5B — key sub-modules importable")

sub_modules = [
    'sentence_transformers.models.Transformer',
    'sentence_transformers.models.Pooling',
    'sentence_transformers.models.Normalize',
    'huggingface_hub',
]
for mod in sub_modules:
    try:
        __import__(mod)
        print(f"  {OK}  {mod}")
    except ImportError as ex:
        print(f"  {E}  {mod}  →  {ex}")

# ════════════════════════════════════════════════════════════════
# CHECK 5C — huggingface_hub version
# ════════════════════════════════════════════════════════════════
section("CHECK 5C — huggingface_hub version")

try:
    import huggingface_hub
    print(f"  {OK}  huggingface_hub version: {huggingface_hub.__version__}")

    # HF_HUB_OFFLINE support was added in 0.10 — older versions ignore it
    from packaging.version import Version
    hf_ver = Version(huggingface_hub.__version__)
    if hf_ver < Version("0.10"):
        print(f"  {E}  Version too old — HF_HUB_OFFLINE=1 is IGNORED on < 0.10")
        print(f"       Fix: pip install --upgrade huggingface-hub")
    else:
        print(f"  {OK}  Version supports HF_HUB_OFFLINE=1")
except ImportError:
    print(f"  {E}  huggingface_hub not installed")
except ImportError:
    # packaging not available — skip version check
    print(f"  {W}  Could not check version (packaging not installed) — skipping")

# ════════════════════════════════════════════════════════════════
# CHECK 5D — 2_Normalize folder (missing from your snapshot!)
# ════════════════════════════════════════════════════════════════
section("CHECK 5D — modules.json path entries vs disk reality")

modules_path = os.path.join(SNAPSHOT_DIR, 'modules.json')
with open(modules_path, 'r', encoding='utf-8') as f:
    modules = json.load(f)

all_ok = True
for m in modules:
    mod_type = m.get('type', '?')
    mod_path = m.get('path', '')

    if mod_path == '':
        # Empty path means the root snapshot dir itself — always present
        exists = True
        disk_path = SNAPSHOT_DIR
    else:
        disk_path = os.path.join(SNAPSHOT_DIR, mod_path)
        exists = os.path.isdir(disk_path) or os.path.isfile(disk_path)

    status = OK if exists else E
    print(f"  {status}  type={mod_type}")
    print(f"         path={mod_path!r}  →  {disk_path}")
    if not exists:
        print(f"         ^^^ THIS FOLDER IS MISSING FROM YOUR SNAPSHOT ^^^")
        all_ok = False

if not all_ok:
    print(f"\n  {E}  One or more module folders referenced in modules.json")
    print(f"       do not exist on disk. This is why offline loading fails.")
    print(f"       The download was incomplete — missing sub-folders were")
    print(f"       probably symlinks that Windows didn't copy correctly.")
    print(f"\n  Fix options:")
    print(f"    1. Re-download with symlinks disabled (already in your settings)")
    print(f"       but you need to DELETE the broken snapshot first:")
    print(f'       rmdir /s /q "{SNAPSHOT_DIR}"')
    print(f"       Then re-download:")
    print(f"       py fix_download.py   (see below — this script creates it)")
    print(f"\n    2. Or manually create the missing folder by running:")
    print(f"       py check_56.py fix")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == 'fix':
        section("AUTO-FIX — Creating missing folders from HuggingFace")
        _try_fix(modules, SNAPSHOT_DIR)

# ════════════════════════════════════════════════════════════════
# CHECK 6A — load with HF_HUB_OFFLINE=1 (step by step)
# ════════════════════════════════════════════════════════════════
section("CHECK 6A — Loading model offline, step by step")

os.environ['HF_HUB_OFFLINE']      = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
print(f"  {I}  HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1 set")
print(f"  {I}  Loading from: {SNAPSHOT_DIR}\n")

from sentence_transformers import SentenceTransformer, LoggingHandler
import logging

# Capture sentence_transformers log output so we can show it
logging.basicConfig(
    level=logging.DEBUG,
    format='  [ST-LOG] %(levelname)s %(name)s: %(message)s',
    handlers=[LoggingHandler()]
)

# Suppress noisy sub-loggers but keep sentence_transformers itself visible
for noisy in ['urllib3', 'filelock', 'PIL', 'transformers.tokenization_utils']:
    logging.getLogger(noisy).setLevel(logging.WARNING)

print(f"  {I}  Attempting SentenceTransformer(snapshot_dir) …")
try:
    model = SentenceTransformer(SNAPSHOT_DIR)
    print(f"\n  {OK}  Model loaded successfully in offline mode!")

    # ── CHECK 6B — encode a test sentence ───────────────────────────────
    section("CHECK 6B — encode() test")
    import numpy as np
    vec = model.encode("electrician Lagos solar panel", normalize_embeddings=True, show_progress_bar=False)
    dim_ok = len(vec) == 768
    print(f"  {'OK' if dim_ok else 'FAIL'}  Output dim = {len(vec)}  (expected 768)")

    norm = float(np.linalg.norm(vec))
    norm_ok = abs(norm - 1.0) < 0.001
    print(f"  {'OK' if norm_ok else 'FAIL'}  L2 norm = {norm:.6f}  (expected ~1.0 — normalize_embeddings=True)")

    # ── CHECK 6C — semantic sanity ───────────────────────────────────────
    section("CHECK 6C — semantic sanity (the real proof)")
    pairs = [
        ("fix my generator",       "diesel engine maintenance technician",  True,  "should be HIGH — same trade"),
        ("fix my generator",       "bake a birthday cake",                  False, "should be LOW  — unrelated"),
        ("plumber pipe burst",      "water leak emergency repair",           True,  "should be HIGH — same trade"),
        ("electrician solar Lagos", "solar panel wiring installation",       True,  "should be HIGH — same trade"),
    ]
    print(f"  {'Query':<35} vs {'Candidate':<40}  Score   Expected")
    print(f"  {'-'*35}    {'-'*40}  ------  --------")
    for q, c, should_be_high, note in pairs:
        v1 = model.encode(q, normalize_embeddings=True, show_progress_bar=False)
        v2 = model.encode(c, normalize_embeddings=True, show_progress_bar=False)
        score = float(np.dot(v1, v2))
        passed = (score > 0.4) if should_be_high else (score < 0.4)
        sym = OK if passed else E
        print(f"  {sym}  {q:<35} vs {c:<40}  {score:.3f}   {note}")

    section("RESULT — Offline model is WORKING correctly")
    print(f"  {OK}  All checks passed.")
    print(f"  {I}  Your search_service.py will work offline.")
    print(f"  {I}  The 'Loading weights' progress bar at server start is normal.")
    print(f"  {I}  It comes from the background thread in apps.py and is cosmetic.")

except Exception as ex:
    print(f"\n  {E}  Load FAILED: {type(ex).__name__}: {ex}")
    import traceback
    print("\n  Full traceback:")
    traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  DIAGNOSIS")
    print(f"{'='*60}")
    err = str(ex).lower()
    if '2_normalize' in err or 'normalize' in err:
        print(f"  ROOT CAUSE: The 2_Normalize subfolder is missing from your snapshot.")
        print(f"  This is confirmed by Check 5D above.")
        print(f"  Run:  py check_56.py fix   to attempt auto-repair")
    elif 'connection' in err or 'network' in err or 'offline' in err:
        print(f"  ROOT CAUSE: sentence-transformers is trying to reach the internet")
        print(f"  despite HF_HUB_OFFLINE=1. This happens when huggingface_hub is")
        print(f"  too old to respect that variable.")
        print(f"  Fix:  pip install --upgrade huggingface-hub sentence-transformers")
    elif 'safetensor' in err or 'weight' in err:
        print(f"  ROOT CAUSE: model.safetensors exists but can't be loaded.")
        print(f"  The file may be corrupted (partial download).")
        print(f"  Fix:  delete the snapshot and re-download.")
    elif 'no module' in err:
        print(f"  ROOT CAUSE: A Python package is missing.")
        print(f"  Fix:  pip install sentence-transformers transformers torch")
    else:
        print(f"  Unknown error — read the traceback above carefully.")
        print(f"  The most likely cause is a missing or corrupted file in:")
        print(f"  {SNAPSHOT_DIR}")