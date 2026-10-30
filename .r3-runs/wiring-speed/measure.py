"""Measure a command's wall time and complete waited process-tree CPU usage."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',required=True)
    parser.add_argument('--cwd',type=Path,default=Path.cwd())
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    output=Path.cwd()/'.r3-runs/wiring-speed'/f'{args.label}-measure.json'
    stop=threading.Event()
    samples=[]
    started=time.perf_counter()
    started_at=time.time()
    proc=subprocess.Popen(command,cwd=args.cwd)

    def sample():
        while not stop.is_set():
            table=subprocess.check_output(['ps','-axo','pid=,ppid='],text=True)
            parents={int(line.split()[0]):int(line.split()[1]) for line in table.splitlines() if line.strip()}
            family={proc.pid}
            while True:
                children={p for p,parent in parents.items() if parent in family}
                if children<=family:
                    break
                family|=children
            samples.append({'timestamp':time.time(),'load':list(os.getloadavg()),
                            'processes':len(family),'machine_processes':len(parents)})
            stop.wait(1.)
    thread=threading.Thread(target=sample,daemon=True)
    thread.start()
    _,status,usage=os.wait4(proc.pid,0)
    wall=time.perf_counter()-started
    proc.returncode=os.waitstatus_to_exitcode(status)
    stop.set()
    thread.join()
    report={'exit_code':proc.returncode,'wall_s':wall,'user_s':usage.ru_utime,'system_s':usage.ru_stime,
            'cpu_s':usage.ru_utime+usage.ru_stime,'cpu_method':'wait4 cumulative process-tree user + system',
            'logical_cpus':os.cpu_count(),'observed_peak_processes':max((s['processes'] for s in samples),default=1),
            'started_at':started_at,'samples':samples}
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2),flush=True)
    raise SystemExit(proc.returncode)


if __name__=='__main__':
    main()
