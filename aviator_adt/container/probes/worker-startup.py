#!/usr/bin/env python3

import os
import sys
import glob

def main():
    found = False
    for pidfile in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            with open(pidfile, 'rb') as f:
                cmdline = f.read().replace(b'\x00', b' ')
                if b'celery' in cmdline:
                    found = True
                    break
        except Exception:
            continue
    if found:
        sys.exit(0)
    else:
        print("Celery process not running")
        sys.exit(1)

if __name__ == "__main__":
    main()