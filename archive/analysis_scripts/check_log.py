#!/usr/bin/env python3
from pymavlink import DFReader
import sys

log = DFReader.DFReader_binary('/home/nandakishore7/ardupilot/logs/00000257.BIN')
while True:
    m = log.recv_msg()
    if m is None:
        break
    if m.get_type() in ('ERR', 'MSG', 'EV', 'MODE', 'STATUSTEXT'):
        print(f"{m.get_type()}: {m}")
