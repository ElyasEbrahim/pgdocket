"""jevbench: Phase 0 benchmark harness.

  fetch       download a CFPB complaint sample with narratives (CC0 mirror)
  load        load the sample into Postgres (bench.complaints, bench.items)
  run         run the experiments through pg-jev against a backend (mock | sim | jev | --api-url)
  analyze     write results/<run_id>/report.md and metrics.json
  cachecheck  show whether pg-jev reuses answers across backends within one session
  determinism compare one experiment across repeated runs (no API calls)
  curve       label learning curve for calibrators on a stored run (no API calls)
  reorder     re-insert bench.items in a seeded order (new batch neighbours), or --restore the original
  batchcontext compare a shuffled-batch run with same-batch runs (no API calls)
  batchsize   compare a single-row run (--batch-size 1) with batched runs (no API calls)
  load-github load Kubernetes issues (from `python -m jevbench.ghissues`) into demo.k8s_issues
  replay      insert recent Kubernetes issues into an empty demo.k8s_live, in order, at a steady rate
  ppivalidate do PPI intervals hold on real model errors? (stored answers, no API calls)
  comparevalidate was `pgdocket compare` right? its verdict against all human labels (no API calls)
  frontier    ideas from recent research, tested on stored answers (no API calls)
  recallvalidate do recall targets hold on real model errors? (no API calls)
  driftvalidate does the drift check alarm when it should, and only then? (no API calls)
"""
import argparse
import json
import sys

from . import demo, fetch, followup, replay, report, runner
from .tasks import default_experiments


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jevbench", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch")
    f.add_argument("--n", type=int, default=3000)
    f.add_argument("--out", default="data/raw/cfpb_sample.jsonl")
    f.add_argument("--seed", type=int, default=7)
    f.add_argument("--max-chars", type=int, default=1500)
    f.add_argument("--source", choices=["parquet", "api"], default="parquet")
    f.add_argument("--shards", default="0", help="parquet shards to sample from (0,1,2), comma-separated")

    l = sub.add_parser("load")
    l.add_argument("--input", default="data/raw/cfpb_sample.jsonl")
    l.add_argument("--n", type=int, default=2000)

    r = sub.add_parser("run")
    r.add_argument("--backend", default="sim", help="mock | sim | jev, or any name with --api-url")
    r.add_argument("--api-url", default=None, help="any /v1/systemone-compatible endpoint (stuntd, laya-server)")
    r.add_argument("--model", default=None)
    r.add_argument("--experiments", default="all", help="comma-separated names, or 'all'")
    r.add_argument("--max-usd", type=float, default=1.0, help="refuse paid runs estimated above this")
    r.add_argument("--yes", action="store_true", help="confirm a paid run")
    r.add_argument("--notes", default=None)
    r.add_argument("--analyze", action="store_true", help="write the report when the run finishes")
    r.add_argument("--batch-size", type=int, default=None, help="rows per API request (pg-jev default: 20)")

    a = sub.add_parser("analyze")
    a.add_argument("--run", default="latest")
    a.add_argument("--out", default="results")

    d = sub.add_parser("determinism")
    d.add_argument("--runs", required=True, help="comma-separated run ids; the first one names the output folder")
    d.add_argument("--experiment", default="choice_base")

    c = sub.add_parser("curve")
    c.add_argument("--run", default="latest")
    c.add_argument("--sizes", default="100,200,500,1000")
    c.add_argument("--repeats", type=int, default=50)

    o = sub.add_parser("reorder")
    o.add_argument("--seed", default="1")
    o.add_argument("--restore", action="store_true")

    bc = sub.add_parser("batchcontext")
    bc.add_argument("--same", required=True, help="comma-separated runs with the original batches")
    bc.add_argument("--shuffled", required=True, help="the run made after `reorder`")
    bc.add_argument("--experiment", default="choice_base")
    bc.add_argument("--reorder-info", default=None, help="JSON printed by `reorder`, embedded in the report")

    bs = sub.add_parser("batchsize")
    bs.add_argument("--batched", required=True, help="comma-separated runs made with 20 rows per request")
    bs.add_argument("--single", required=True, help="the run made with --batch-size 1")
    bs.add_argument("--shuffled", default=None, help="optional shuffled-batch run from the batch-context check")
    bs.add_argument("--experiment", default="choice_base")

    lg = sub.add_parser("load-github")
    lg.add_argument("--input", default="data/raw/github_kubernetes.jsonl")

    rp = sub.add_parser("replay")
    rp.add_argument("--n", type=int, default=300)
    rp.add_argument("--rate", type=float, default=10.0, help="issues per second")
    rp.add_argument("--options-file", default="examples/k8s_sig_options.json", help="SIGs in scope")
    rp.add_argument("--create-only", action="store_true", help="only (re)create the empty demo.k8s_live table")

    sub.add_parser("ppivalidate")
    cv = sub.add_parser("comparevalidate")
    cv.add_argument("--version", type=int, default=2, help="the k8s_route version compared with the active one")
    cv.add_argument("--out", default="results/compare-k8s-descriptions")
    sub.add_parser("frontier")
    sub.add_parser("recallvalidate")
    sub.add_parser("driftvalidate")
    sub.add_parser("cachecheck")
    sub.add_parser("experiments")

    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        if args.source == "parquet":
            shards = tuple(int(s) for s in args.shards.split(","))
            m = fetch.fetch_sample_parquet(args.n, args.out, seed=args.seed, max_chars=args.max_chars, shards=shards)
        else:
            m = fetch.fetch_sample_api(args.n, args.out, seed=args.seed, max_chars=args.max_chars)
        print(json.dumps(m, indent=2))
    elif args.cmd == "load":
        n, dist = runner.load_sample(args.input, args.n)
        print(f"loaded {n} complaints")
        for product, count in dist:
            print(f"  {count:>6}  {product}")
    elif args.cmd == "run":
        exps = default_experiments()
        if args.experiments != "all":
            wanted = set(args.experiments.split(","))
            exps = [e for e in exps if e.name in wanted]
            missing = wanted - {e.name for e in exps}
            if missing:
                sys.exit(f"unknown experiments: {sorted(missing)}")
        run_id = runner.run(args.backend, args.model, args.api_url, exps, args.max_usd, args.notes, args.yes,
                            args.batch_size)
        print(f"run_id: {run_id}")
        if args.analyze:
            out_dir, _ = report.analyse(run_id)
            print(f"report: {out_dir}/report.md")
    elif args.cmd == "analyze":
        out_dir, _ = report.analyse(args.run, args.out)
        print(f"report: {out_dir}/report.md")
    elif args.cmd == "determinism":
        runs = args.runs.split(",")
        d = followup.determinism(runs, args.experiment)
        print("report:", followup.write(d, followup.render_determinism(d),
                                        f"results/{runs[0]}/determinism_{args.experiment}"))
    elif args.cmd == "curve":
        with report.connect() as conn:
            run_id = args.run if args.run != "latest" else report.latest_run_id(conn)
        sizes = tuple(int(x) for x in args.sizes.split(","))
        c = followup.learning_curve(run_id, sizes, args.repeats)
        print("report:", followup.write(c, followup.render_curve(c), f"results/{run_id}/learning_curve"))
    elif args.cmd == "reorder":
        print(json.dumps(runner.reorder(seed=args.seed, restore=args.restore), indent=2))
    elif args.cmd == "batchcontext":
        same = args.same.split(",")
        b = followup.batch_context(same, args.shuffled, args.experiment,
                                   json.loads(args.reorder_info) if args.reorder_info else None)
        print("report:", followup.write(b, followup.render_batch_context(b),
                                        f"results/{same[0]}/batch_context_{args.experiment}"))
    elif args.cmd == "batchsize":
        batched = args.batched.split(",")
        b = followup.batch_size_compare(batched, args.single, args.shuffled, args.experiment)
        print("report:", followup.write(b, followup.render_batch_size(b),
                                        f"results/{batched[0]}/batch_size_{args.experiment}"))
    elif args.cmd == "load-github":
        print(json.dumps(demo.load_github(args.input), indent=2))
    elif args.cmd == "replay":
        if args.create_only:
            replay.create_live_table()
            print("demo.k8s_live created (empty)")
        else:
            sigs = [o["name"] for o in json.load(open(args.options_file))]
            print(json.dumps(replay.replay(args.n, args.rate, sigs), indent=2))
    elif args.cmd == "ppivalidate":
        from . import ppivalidate
        print("report:", ppivalidate.run() + "/report.md")
    elif args.cmd == "recallvalidate":
        from . import recallvalidate
        print("report:", recallvalidate.run() + "/report.md")
    elif args.cmd == "driftvalidate":
        from . import driftvalidate
        print("report:", driftvalidate.run() + "/report.md")
    elif args.cmd == "frontier":
        from . import frontier
        print("report:", frontier.run() + "/report.md")
    elif args.cmd == "comparevalidate":
        from . import comparevalidate
        print("report:", comparevalidate.run(version=args.version, out_dir=args.out) + "/validation.md")
    elif args.cmd == "cachecheck":
        print(json.dumps(runner.cache_check(), indent=2))
    elif args.cmd == "experiments":
        for e in default_experiments():
            print(f"{e.name:<24} {e.kind:<7} {e.notes or e.question}")


if __name__ == "__main__":
    main()
