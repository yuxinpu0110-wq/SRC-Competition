"""Export only baseline strategy/config files from the shared Git ancestor.

No runtime binaries or large logs are exported. Use a fresh output directory;
this is a source snapshot, not an unrelated git repository to push from.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BASELINE='142da5a303a1039daa486a42e6a286a975520444'


def selected(name):
    path=Path(name)
    return (name in ('match.json','opponent.json','start-v2.1.cmd')
            or name.startswith('configs/') and path.suffix == '.json'
            or name.startswith('ZBin/PythonScripts/') and path.suffix in ('.py','.pyi','.json','.ini','.qml')
            or name.startswith(('ZBin/data/','ZBin/defence_config/'))
            or name.startswith('ZBin/') and len(path.parts) == 2 and path.suffix == '.ini')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'tmp/v21-frozen-142da5a')
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():
        parser.error('Use a new output directory; baseline export never overwrites existing files')
    result=subprocess.run(['git','ls-tree','-r','--name-only','-z',BASELINE],cwd=ROOT,
                          capture_output=True,check=True)
    names=[n.decode('utf-8') for n in result.stdout.split(b'\0') if n and selected(n.decode('utf-8'))]
    hashes={}
    # Lazy fetches, if needed, are only for these selected source/config blobs.
    for name in names:
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid repository path')
        data=subprocess.run(['git','show',f'{BASELINE}:{name}'],cwd=ROOT,
                            capture_output=True,check=True).stdout
        target=output/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    (output/'baseline-source-manifest.json').write_text(json.dumps(dict(
        baseline_commit=BASELINE,repository='https://github.com/yuxinpu0110-wq/SRC-Competition',
        contains_runtime=False,source_sha256=hashes),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Frozen V2.1 source: {output}. Install its matching official runtime separately.')


if __name__=='__main__':
    main()
