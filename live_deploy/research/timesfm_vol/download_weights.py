"""Download the TimesFM weights the experiment ran on, at the exact
Hugging Face revisions it used, into a folder of your choosing. The
weights stay out of git: 3.0's licence forbids distributing them, and at
1.3 GB and 0.9 GB they're far past what a repository should hold. Run
this instead, and you get the same bytes.

    python download_weights.py ~/timesfm-weights
    python forecast_timesfm.py vol_history.csv.gz \
        --weights3 ~/timesfm-weights/timesfm-3.0-pytorch \
        --weights25 ~/timesfm-weights/timesfm-2.5-200m-pytorch

  google/timesfm-3.0-pytorch       TimesFM Non-Commercial License v1.0:
                                   non-commercial, non-production use;
                                   no redistribution
  google/timesfm-2.5-200m-pytorch  Apache-2.0
"""

from __future__ import annotations

import argparse
import os
import sys

# (Hugging Face repo, the commit the experiment used)
MODELS = {
    "timesfm-3.0-pytorch": ("google/timesfm-3.0-pytorch", "43046b85ec22d584a13f8098c2ed39c889e129c2"),
    "timesfm-2.5-200m-pytorch": ("google/timesfm-2.5-200m-pytorch", "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="where to put the weights (a folder per model)")
    ap.add_argument("--only", choices=sorted(MODELS), help="one model only")
    args = ap.parse_args(argv)

    from huggingface_hub import snapshot_download
    for name, (repo, revision) in MODELS.items():
        if args.only and name != args.only:
            continue
        dest = os.path.join(args.folder, name)
        print(f"{repo} @ {revision[:12]} → {dest}", file=sys.stderr, flush=True)
        snapshot_download(repo_id=repo, revision=revision, local_dir=dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
