"""
check_offline_model.py
======================
Run this from your project root (same folder as manage.py):

    py check_offline_model.py

It checks every layer of the offline model loading chain and tells you
exactly where it breaks — no guessing.
"""

import os
import sys

# ── 0. Replicate exactly what settings.py does ──────────────────────────────
os.environ.setdefault('HUGGINGFACE_HUB_SYMLINKS_MODE', 'copy')
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')

SNAPSHOT_HASH = 'e8c3b32edf5434bc2275fc9bab85f82640a19130'

os.environ.setdefault('HF_TOKEN', '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')
os.environ.setdefault('TRANSFORMERS_CACHE',          os.path.join(os.path.expanduser('~'), '.hf_cache'))
os.environ.setdefault('HF_HOME',                     os.path.join(os.path.expanduser('~'), '.hf_cache'))
os.environ.setdefault('SENTENCE_TRANSFORMERS_HOME',  os.path.join(os.path.expanduser('~'), '.hf_cache'))

MODEL_PATH = os.path.join(
    os.path.expanduser('~'),
    '.hf_cache', 'hub',
    'models--sentence-transformers--all-mpnet-base-v2',
    'snapshots',
    SNAPSHOT_HASH,
)
os.environ.setdefault('TEXT_ENCODER_MODEL_PATH', MODEL_PATH)

# ── Helpers ──────────────────────────────────────────────────────────────────

W = "\033[93m[WARN] \033[0m"   # yellow
E = "\033[91m[FAIL] \033[0m"   # red
OK = "\033[92m[ OK ] \033[0m"  # green
I = "\033[94m[INFO] \033[0m"   # blue

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def check(label, condition, detail=""):
    symbol = OK if condition else E
    print(f"  {symbol} {label}")
    if detail:
        print(f"         {detail}")
    return condition

# ── CHECK 1: Resolved paths ──────────────────────────────────────────────────
section("CHECK 1 — Resolved environment paths")

home = os.path.expanduser('~')
print(f"  {I} Home dir              : {home}")
print(f"  {I} HF_HOME               : {os.environ.get('HF_HOME')}")
print(f"  {I} TEXT_ENCODER_MODEL_PATH: {os.environ.get('TEXT_ENCODER_MODEL_PATH')}")

# ── CHECK 2: Snapshot directory exists ───────────────────────────────────────
section("CHECK 2 — Snapshot directory on disk")

snapshot_dir = os.environ['TEXT_ENCODER_MODEL_PATH']
dir_exists = os.path.isdir(snapshot_dir)
check("Snapshot directory exists", dir_exists, snapshot_dir)

if not dir_exists:
    # Try to find where the model actually downloaded to
    hub_root = os.path.join(home, '.hf_cache', 'hub')
    model_root = os.path.join(hub_root, 'models--sentence-transformers--all-mpnet-base-v2')
    snapshots_dir = os.path.join(model_root, 'snapshots')

    print(f"\n  {W} Directory not found. Searching for alternatives…")
    print(f"  {I} Hub root      : {hub_root}  →  exists={os.path.isdir(hub_root)}")
    print(f"  {I} Model root    : {model_root}  →  exists={os.path.isdir(model_root)}")
    print(f"  {I} Snapshots dir : {snapshots_dir}  →  exists={os.path.isdir(snapshots_dir)}")

    if os.path.isdir(snapshots_dir):
        hashes = os.listdir(snapshots_dir)
        if hashes:
            print(f"\n  {W} Found these snapshot hashes instead:")
            for h in hashes:
                print(f"         → {h}")
            print(f"\n  {E} Your SNAPSHOT_HASH in settings.py is:")
            print(f"         '{SNAPSHOT_HASH}'")
            print(f"\n  Fix: change SNAPSHOT_HASH in settings.py to one of the hashes above.")
        else:
            print(f"  {E} Snapshots directory exists but is EMPTY — model never downloaded.")
    else:
        print(f"  {E} Model was never downloaded to the expected location.")
        print(f"      Run this once to download it:")
        print(f"      py -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-mpnet-base-v2')\"")
    sys.exit(1)

# ── CHECK 3: Required files inside the snapshot ──────────────────────────────
section("CHECK 3 — Required files inside snapshot directory")

REQUIRED_FILES = [
    'modules.json',           # tells SentenceTransformer how to assemble the model
    'config.json',            # transformer config
    'tokenizer_config.json',  # tokenizer config
    'sentence_bert_config.json',  # sentence-transformers specific
]
REQUIRED_SUBFOLDERS = [
    '1_Pooling',              # pooling layer config
]
WEIGHT_EXTENSIONS = ('.bin', '.safetensors', '.pt')

all_files = os.listdir(snapshot_dir)
print(f"  {I} Files in snapshot dir ({len(all_files)} total):")
for f in sorted(all_files):
    size = os.path.getsize(os.path.join(snapshot_dir, f))
    is_weight = f.endswith(WEIGHT_EXTENSIONS)
    tag = " ← model weights" if is_weight else ""
    print(f"         {f}  ({size:,} bytes){tag}")

print()
all_ok = True
for fname in REQUIRED_FILES:
    fpath = os.path.join(snapshot_dir, fname)
    ok = os.path.isfile(fpath)
    check(f"File present: {fname}", ok)
    all_ok = all_ok and ok

for folder in REQUIRED_SUBFOLDERS:
    fpath = os.path.join(snapshot_dir, folder)
    ok = os.path.isdir(fpath)
    check(f"Folder present: {folder}/", ok)
    all_ok = all_ok and ok

# Check weights exist (any recognised weight format)
weight_files = [f for f in all_files if f.endswith(WEIGHT_EXTENSIONS)]
has_weights = len(weight_files) > 0
check(f"Weight file(s) present ({', '.join(weight_files) or 'NONE'})", has_weights)
all_ok = all_ok and has_weights

if not all_ok:
    print(f"\n  {E} Missing files mean the model is incomplete (partial download).")
    print(f"      Delete the snapshot folder and re-download:")
    print(f"      rmdir /s /q \"{snapshot_dir}\"")
    print(f"      py -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-mpnet-base-v2')\"")

# ── CHECK 4: modules.json contents ───────────────────────────────────────────
section("CHECK 4 — modules.json is valid JSON")

modules_path = os.path.join(snapshot_dir, 'modules.json')
if os.path.isfile(modules_path):
    try:
        import json
        with open(modules_path, 'r', encoding='utf-8') as f:
            modules = json.load(f)
        check("modules.json parses as valid JSON", True)
        print(f"  {I} Module entries: {len(modules)}")
        for m in modules:
            print(f"         → type={m.get('type','?')}  path={m.get('path','?')}")
    except Exception as ex:
        check("modules.json parses as valid JSON", False, str(ex))
else:
    print(f"  {W} modules.json not found — skipping this check.")

# ── CHECK 5: sentence_transformers package importable ────────────────────────
section("CHECK 5 — sentence_transformers package")

try:
    import sentence_transformers
    check(
        f"sentence_transformers importable (version {sentence_transformers.__version__})",
        True,
    )
except ImportError as ex:
    check("sentence_transformers importable", False, str(ex))
    print(f"\n  Fix:  pip install sentence-transformers")
    sys.exit(1)

# ── CHECK 6: Force offline, then load the model ──────────────────────────────
section("CHECK 6 — Load model in OFFLINE mode (the real test)")

# Mirror exactly what text_encoder.py does
os.environ['HF_HUB_OFFLINE']      = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

print(f"  {I} HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1  (network blocked)")
print(f"  {I} Loading from: {snapshot_dir}")
print(f"  {I} This may take 5–30 seconds on first run …\n")

try:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(snapshot_dir)
    check("SentenceTransformer loaded successfully in offline mode", True)

    # Quick encode test
    vec = model.encode("electrician Lagos solar panel", normalize_embeddings=True, show_progress_bar=False)
    check(f"encode() works — output dim={len(vec)}", len(vec) == 768)

    # Similarity sanity check
    import numpy as np
    v1 = model.encode("fix my generator", normalize_embeddings=True, show_progress_bar=False)
    v2 = model.encode("diesel engine maintenance technician", normalize_embeddings=True, show_progress_bar=False)
    v3 = model.encode("bake a birthday cake", normalize_embeddings=True, show_progress_bar=False)
    sim_related   = float(np.dot(v1, v2))
    sim_unrelated = float(np.dot(v1, v3))
    check(
        f"Semantic similarity looks sane  (related={sim_related:.3f} > unrelated={sim_unrelated:.3f})",
        sim_related > sim_unrelated,
    )

except Exception as ex:
    check("SentenceTransformer loaded successfully in offline mode", False)
    print(f"\n  {E} Error detail:\n    {type(ex).__name__}: {ex}")

    # Common failure reasons
    if 'modules.json' in str(ex):
        print(f"\n  {E} ROOT CAUSE: modules.json is missing or unreadable.")
        print(f"      The snapshot downloaded without this critical file.")
        print(f"      Fix: re-download the model (see Check 3 above).")
    elif 'offline' in str(ex).lower() or 'network' in str(ex).lower() or 'connection' in str(ex).lower():
        print(f"\n  {E} ROOT CAUSE: sentence-transformers tried to phone home despite HF_HUB_OFFLINE=1.")
        print(f"      This usually means modules.json references external paths,")
        print(f"      or you have an old version of sentence-transformers.")
        print(f"      Fix: pip install --upgrade sentence-transformers huggingface-hub")
    elif 'No such file' in str(ex):
        print(f"\n  {E} ROOT CAUSE: A file referenced inside modules.json doesn't exist on disk.")
        print(f"      The download is incomplete.")
    sys.exit(1)

# ── CHECK 7: text_encoder.py singleton (as used in search_service.py) ────────
section("CHECK 7 — text_encoder.py singleton (production code path)")

# We need to fake enough of Django's environment so the import works standalone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    # Reset HF_HUB_OFFLINE so text_encoder._ensure_loaded() sets it itself
    # (mirrors the production flow)
    os.environ.pop('HF_HUB_OFFLINE', None)
    os.environ.pop('TRANSFORMERS_OFFLINE', None)

    from jobs.service.text_encoder import text_encoder as te
    te._ensure_loaded()
    check("text_encoder singleton loaded via production import path", True)

    vec = te.encode("plumber pipe burst emergency Lagos")
    check(f"text_encoder.encode() returns 768-dim vector", len(vec) == 768)

    scores = te.batch_cosine_similarity(vec, [vec, [0.0]*768])
    check(
        f"batch_cosine_similarity works  (self-similarity={scores[0]:.4f}, should be ~1.0)",
        abs(scores[0] - 1.0) < 0.001,
    )

except Exception as ex:
    check("text_encoder singleton loaded via production import path", False)
    print(f"\n  {E} {type(ex).__name__}: {ex}")
    print(f"\n  Note: if this says 'No module named jobs', that is expected when")
    print(f"  running outside Django. Checks 1–6 above are the important ones.")

# ── SUMMARY ──────────────────────────────────────────────────────────────────
section("SUMMARY")
print(f"  If all checks above show {OK.strip()} the model is working offline.")
print(f"  If any check shows {E.strip()} fix that step first and re-run this script.")
print(f"\n  Common root causes on Windows:")
print(f"    1. SNAPSHOT_HASH in settings.py doesn't match the folder on disk")
print(f"    2. modules.json missing — incomplete HuggingFace download")
print(f"    3. sentence-transformers version too old (pip install --upgrade sentence-transformers)")
print(f"    4. Symlink issues — real files not copied (HUGGINGFACE_HUB_SYMLINKS_MODE=copy not applied in time)")
print()