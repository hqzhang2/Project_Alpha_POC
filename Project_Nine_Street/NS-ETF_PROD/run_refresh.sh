#!/bin/bash
# NS-ETF daily refresh — weekday after close (17:45 ET, after NS-7's 17:30).
# env -i equivalent: launchd gives a clean env; set HOME explicitly.
export HOME=/Users/chuck
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-ETF_PROD
exec /usr/bin/env -u PYTHONPATH \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 \
  pipeline.py
