"""Parse an MTGA Player.log locally, writing every event the 17Lands client would
have uploaded to JSONL files instead. Nothing is sent over the network.

Depends on the seventeenlands package from a checkout of
https://github.com/rconroy293/mtga-log-client - point MTGA_CLIENT_SRC at its
src/python directory if it does not live at the default path below.
"""

import argparse
import collections
import json
import os
import pathlib
import sys

DEFAULT_CLIENT_SRC = os.path.expanduser("~/mtga-log-client/src/python")
CLIENT_SRC = os.environ.get("MTGA_CLIENT_SRC", DEFAULT_CLIENT_SRC)

if not os.path.isdir(CLIENT_SRC):
    sys.exit(
        f"Cannot find the seventeenlands source at {CLIENT_SRC}\n"
        f"Clone https://github.com/rconroy293/mtga-log-client and set "
        f"MTGA_CLIENT_SRC to its src/python directory."
    )

sys.path.insert(0, CLIENT_SRC)

import seventeenlands.mtga_follower as mf  # noqa: E402

# Fields injected into every blob by _add_base_api_data that carry no game data.
NOISE_FIELDS = ("token", "client_version")


class LocalRecorder:
    """Stands in for ApiClient. Captures submit_* calls to disk instead of POSTing.

    No caller in mtga_follower uses a submit_* return value, so returning None
    is safe.
    """

    def __init__(self, outdir: pathlib.Path, keep_token: bool) -> None:
        self.outdir = outdir
        self.keep_token = keep_token
        self.counts: collections.Counter = collections.Counter()
        self._handles: dict = {}

    def __getattr__(self, name: str):
        if not name.startswith("submit_"):
            raise AttributeError(name)
        kind = name[len("submit_") :]

        def record(blob, *args, **kwargs):
            if kind == "error_info":  # client-side crash reports, not game data
                return None
            if not self.keep_token and isinstance(blob, dict):
                blob = {k: v for k, v in blob.items() if k not in NOISE_FIELDS}
            handle = self._handles.get(kind)
            if handle is None:
                handle = open(self.outdir / f"{kind}.jsonl", "w", encoding="utf8")
                self._handles[kind] = handle
            handle.write(json.dumps(blob, default=str) + "\n")
            self.counts[kind] += 1
            return None

        return record

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", help="Path to Player.log")
    parser.add_argument("-o", "--outdir", default="mtga_out", help="Output directory")
    parser.add_argument(
        "--keep-token",
        action="store_true",
        help="Keep the token/client_version fields in the output (stripped by default)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.log_file):
        sys.exit(f"No such log file: {args.log_file}")

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    recorder = LocalRecorder(outdir, keep_token=args.keep_token)
    # Token is only ever echoed back into the blobs, never used for local parsing.
    # The host is deliberately unroutable as a second layer of defence.
    follower = mf.Follower(token="local-only", host="http://localhost.invalid")
    follower._api_client = recorder

    follower.parse_log(filename=args.log_file, follow=False)
    recorder.close()

    total = sum(recorder.counts.values())
    print(f"\n{total} events -> {outdir.resolve()}")
    for kind, count in recorder.counts.most_common():
        print(f"  {count:6d}  {kind}.jsonl")
    if not total:
        print("  (nothing parsed - is Detailed Logs enabled in Arena?)")


if __name__ == "__main__":
    main()