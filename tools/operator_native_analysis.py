"""Show native recovery status; new exports require an explicit operation."""
import json
from pathlib import Path
import sys

def main():
    if "--run-rea" in sys.argv:
        sys.argv.remove("--run-rea")
        from native_batch_export import main as run
        return run()
    if "--run-bounded" in sys.argv:
        sys.argv.remove("--run-bounded")
        from resume_native_bounded import main as run
        return run()
    root = Path(__file__).resolve().parents[1]
    aggregate = root / "evidence/rea/native-union-catalog.summary.json"
    print("Stato recupero nativo locale. Nessuna nuova analisi avviata.\n")
    if aggregate.is_file():
        record = json.loads(aggregate.read_text(encoding="utf-8"))
        print(json.dumps(record["summary"], indent=2))
        print(f"\nIndice: {aggregate}")
    else:
        for path in sorted((root / "evidence/rea").glob("native-export-*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if "run_id" not in record:
                continue
            print(f"{record['run_id']}: {record.get('succeeded', 0)} pseudocodici; complete={record['complete']}")
            print(f"  {record['output_directory']}")
    print(f"\nMetodo e recovery con timeout reale: {root / 'docs/native-findings.md'}")
    print("Operazioni esplicite: --run-rea [argomenti] oppure --run-bounded [argomenti].")
    return 0

if __name__ == "__main__":
    sys.exit(main())
