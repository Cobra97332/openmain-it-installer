#!/usr/bin/env python3
"""Test-image overlay ONLY. Never applied by the installer/native patch builder.

Exact anchors fail on upstream drift. No injected path can authorize a packet:
maps are corrupted/withheld, readiness invalidated, or real I/O made to fail.
"""
from pathlib import Path
import shutil
import sys


def apply(root):
    here = Path(__file__).resolve().parent
    def replace(name, old, new):
        path = root / name
        text = path.read_text()
        if text.count(old) != 1:
            raise RuntimeError('lab_injector_anchor_mismatch:' + name)
        path.write_text(text.replace(old, new))

    replace('client/internal/engine.go', 'type Engine struct {',
            'type Engine struct {\n\tbinatLab *binatLabState // disposable test image only')
    replace('client/internal/engine.go', '\te.receiveManagementEvents()',
            '\tif err := e.startBINATLab(); err != nil { return err }\n\te.receiveManagementEvents()')
    replace('client/internal/engine.go', '\t\tif networkMap == nil || networkMap.GetSerial() < e.networkSerial {',
            '\t\tvar labErr error\n\t\tnetworkMap, labErr = e.binatLabMap(networkMap)\n'
            '\t\tif labErr != nil { return labErr }\n'
            '\t\tif networkMap == nil || networkMap.GetSerial() < e.networkSerial {')
    replace('client/internal/engine.go', '\t\tif err := e.binatGate.Open(binatEpoch); err != nil {',
            '\t\tif err := e.binatLabBeforeOpen(); err != nil { return err }\n'
            '\t\tif err := e.binatGate.Open(binatEpoch); err != nil {')
    name = 'client/firewall/nftables/manager_linux.go'
    replace('client/firewall/nftables/filter_linux.go',
            '\tif err := r.conn.Flush(); err != nil {\n\t\tr.dropNetworkMatch(exprs)',
            '\tif err := labFlush(r.conn); err != nil {\n\t\tr.dropNetworkMatch(exprs)')
    replace(name, '\tchains, err := m.rConn.ListChains()\n\tif err != nil {\n\t\treturn fmt.Errorf("BINAT native chain readback failed")',
            '\tchains, err := m.rConn.ListChains()\n'
            '\tif err == nil && labReadbackLost() { return fmt.Errorf("BINAT lab discarded native chain readback") }\n'
            '\tif err != nil {\n\t\treturn fmt.Errorf("BINAT native chain readback failed")')
    shutil.copyfile(here / 'engine.go', root / 'client/internal/binat_lab_injector_linux.go')
    shutil.copyfile(here / 'netlink.go', root / 'client/firewall/nftables/binat_lab_injector_linux.go')


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
