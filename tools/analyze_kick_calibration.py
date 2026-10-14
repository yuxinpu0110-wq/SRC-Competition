"""离线读取真实 JSONL；无物理样本时输出 NO_SAMPLES，不填猜测参数。"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
from kick_calibration import analyze


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,default=ROOT/'验收记录/kick_calibration.json')
    args=parser.parse_args()
    reports=[]
    # 每次进程的事务号会从1开始，分日志分析，禁止跨进程合并同名ID。
    for path in args.logs:
        with path.open(encoding='utf-8') as stream:
            rows=[json.loads(line) for line in stream if line.endswith('\n')]
        reports.append(dict(log=str(path.resolve()),result=analyze(rows)))
    output=dict(schema_version=1,usable_for_planning=False,runs=reports)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Calibration diagnostic written to {args.output}')


if __name__=='__main__':
    main()
