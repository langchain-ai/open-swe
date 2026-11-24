#!/bin/sh
metrics report --week 47 | sort -k2 -n | tail -1 | awk '{print $1}' > /workspace/answer.txt
