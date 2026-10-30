"""Create a read-only source snapshot for baseline tests, without a Git checkout."""
from pathlib import Path
import os
import subprocess

root=Path.cwd()
ref='143518cb50dcf431d37e87c1935915ce2310982c'
target=root/'.r3-runs/wiring-speed/baseline-suite-project'
changed=set(subprocess.check_output(['git','diff','--name-only',ref,'--'],text=True).splitlines())
# Include committed task changes, too.
changed.update(subprocess.check_output(['git','diff','--name-only',ref,'HEAD'],text=True).splitlines())
files=subprocess.check_output(['git','ls-tree','-r','--name-only',ref],text=True).splitlines()
for name in files:
    source=root/name
    destination=target/name
    destination.parent.mkdir(parents=True,exist_ok=True)
    if name in changed or not source.exists():
        destination.write_bytes(subprocess.check_output(['git','show',f'{ref}:{name}']))
    else:
        os.link(source,destination,follow_symlinks=False)
print('Baseline source files:',len(files),flush=True)
