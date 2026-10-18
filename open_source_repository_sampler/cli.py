"""open-source-repository-sampler: draw, use and verify reproducible samples of repositories.

    open-source-repository-sampler sample   --langs java --target 30 --seed 1 --out-dir sample
    open-source-repository-sampler checkout sample --dest repos [--deep]
    open-source-repository-sampler run      sample [--deep] -- my-experiment {path} -o out/{full_name}
    open-source-repository-sampler verify   sample --fetch
"""
from __future__ import annotations

import argparse
import sys

from . import checkout, frame, provenance, sample


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _sample_args(sp: argparse.ArgumentParser) -> None:
    d = sample.DEFAULTS
    g = sp.add_argument_group("what to sample")
    g.add_argument("--langs", type=_list, default=d["langs"],
                   help=f"comma list of languages (default all: {','.join(frame.LANGS)})")
    g.add_argument("--tiers", type=_list, default=d["tiers"],
                   help="comma list of popularity tiers: "
                        + ", ".join(f"{k} ({v})" for k, v in frame.TIERS.items())
                        + " (default all)")
    g.add_argument("--target", type=int, default=d["target"],
                   help=f"usable repositories wanted per language (default {d['target']})")
    g.add_argument("--seed", type=int, default=d["seed"],
                   help=f"the one value that fixes the draw; fix it before the first draw and "
                        f"keep the first sample (default {d['seed']})")
    g.add_argument("--exclude-owners", type=_list, default=d["exclude_owners"],
                   help="comma list of GitHub owners never sampled, e.g. your own (a "
                        "conflict-of-interest guard; default none)")
    c = sp.add_argument_group("criteria a repository must meet to be usable")
    for p, col, desc in sample.CRITERIA:
        typ = float if isinstance(d[p], float) else int
        c.add_argument("--" + p.replace("_", "-"), type=typ, default=d[p],
                       help=f"minimum {desc} (default {d[p]})")
    r = sp.add_argument_group("output and run control")
    r.add_argument("--out-dir", default=".",
                   help="directory for the sample files (default: the current directory)")
    r.add_argument("--label", default=None,
                   help="optional label in the file names, to keep several samples side by side")
    r.add_argument("--max-clones", type=int, default=d["max_clones"],
                   help=f"safety stop on total candidates drawn (default {d['max_clones']})")
    r.add_argument("--pause", type=float, default=d["pause"],
                   help=f"seconds between GitHub searches (default {d['pause']})")
    r.add_argument("--allow-dirty", action="store_true",
                   help="run even though the sampler has uncommitted changes (recorded)")
    r.add_argument("--overwrite", action="store_true",
                   help="replace existing sample files in --out-dir")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    command = []
    if "--" in argv:                      # `run ... -- <experiment command>`
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog=provenance.NAME, description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="store_true", help="print the sampler version and commit")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("sample", help="draw a sample (writes the CSV and its derivation)")
    _sample_args(sp)

    cp = sub.add_parser("checkout", help="clone a sample's usable repositories at their commits")
    cp.add_argument("sample", help="the sample CSV, or the directory holding it")
    cp.add_argument("--dest", default="repos", help="directory to clone into (default repos)")
    cp.add_argument("--langs", type=_list, default=None, help="only these detected languages")
    cp.add_argument("--deep", action="store_true",
                    help="every commit up to each pinned commit (default: that commit only)")

    rp = sub.add_parser(
        "run", help="run an experiment command on each usable repository, one clone at a time",
        usage=f"{provenance.NAME} run SAMPLE [options] -- COMMAND [ARGS with {{path}} ...]",
        description="Clone each usable repository at its pinned commit, run the command given "
                    "after `--` on it, then delete the clone. In the command, {path}, {url}, "
                    "{commit}, {full_name}, {language} and {deep} are replaced by the "
                    "repository's values, which are also in the environment as "
                    "SAMPLED_REPOSITORY_PATH, _URL, _COMMIT, _FULL_NAME, _LANGUAGE and _DEEP.")
    rp.add_argument("sample", help="the sample CSV, or the directory holding it")
    rp.add_argument("--deep", action="store_true",
                    help="every commit up to each pinned commit (default: that commit only)")
    rp.add_argument("--langs", type=_list, default=None, help="only these detected languages")
    rp.add_argument("--work-dir", default=None,
                    help="where clones are made (default: a temporary directory)")
    rp.add_argument("--keep", action="store_true", help="keep each clone after its run")
    rp.add_argument("--run-log", default=None,
                    help="JSON Lines file of outcomes; a re-run skips repositories it records "
                         "as done, so an interrupted run resumes")
    rp.add_argument("--fail-fast", action="store_true", help="stop at the first failure")

    vp = sub.add_parser("verify", help="check a sample's CSV against its derivation")
    vp.add_argument("sample", help="the sample CSV, or the directory holding it")
    vp.add_argument("--fetch", action="store_true",
                    help="also check every pinned commit can still be fetched")

    args = ap.parse_args(argv)
    if args.version:
        s = provenance.sampler()
        print(f"{s['name']} {s['version']} @ {s['commit']}" + ("  (dirty)" if s["dirty"] else ""))
        return 0
    if args.cmd == "sample":
        params = {k: getattr(args, k) for k in sample.DEFAULTS}
        try:
            sample.run(params, args.out_dir, args.label, argv, args.allow_dirty, args.overwrite)
        except (RuntimeError, ValueError, FileExistsError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "checkout":
        res = checkout.checkout(args.sample, args.dest, args.langs, args.deep)
        return 1 if any(r["error"] for r in res) else 0
    if args.cmd == "run":
        if not command:
            print("ERROR: give the experiment command after `--`", file=sys.stderr)
            return 2
        res = checkout.for_each(args.sample, checkout.command_experiment(command), args.deep,
                                args.langs, args.work_dir, args.keep, args.run_log,
                                args.fail_fast)
        return 1 if any(r["status"] == "failed" for r in res) else 0
    if args.cmd == "verify":
        problems = checkout.verify(args.sample, args.fetch)
        for p in problems:
            print(f"PROBLEM {p}", file=sys.stderr)
        return 1 if problems else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
