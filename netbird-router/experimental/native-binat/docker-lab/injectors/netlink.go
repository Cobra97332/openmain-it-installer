//go:build linux && !android

package nftables

import (
	"errors"
	"github.com/google/nftables"
	"github.com/google/nftables/expr"
	"github.com/netbirdio/netbird/client/firewall/binat"
	"os"
	"syscall"
)

// Root-only one-shot fault files; the test requests failures, never permissions.
func labConsume(name string) bool {
	if os.Getenv("OPENMAIN_LAB_INJECTORS") != "1" || os.Getenv("OPENMAIN_ISOLATED_TEST") != "1" {
		return false
	}
	if os.Remove(binat.Root+"/fault-"+name) != nil {
		return false
	}
	return true
}

func labNetlinkACK() error {
	if !labConsume("ack") {
		return nil
	}
	// A REAL atomic netlink transaction to a deliberately nonexistent table.
	// Kernel ENOENT is returned to the actual native ACL apply path. No rule is installed.
	conn := &nftables.Conn{}
	table := &nftables.Table{Family: nftables.TableFamilyIPv4, Name: "openmain_lab_absent_ack_target"}
	chain := &nftables.Chain{Table: table, Name: "absent"}
	conn.AddRule(&nftables.Rule{Table: table, Chain: chain, Exprs: []expr.Any{&expr.Counter{}}})
	err := conn.Flush()
	if err == nil {
		return errors.New("lab ACK fault fixture unexpectedly succeeded")
	}
	if errors.Is(err, syscall.ENOENT) {
		os.WriteFile(binat.Root+"/hit-ack", []byte("kernel-ENOENT\n"), 0600)
	}
	return err
}

func labReadbackLost() bool {
	if !labConsume("readback") {
		return false
	}
	os.WriteFile(binat.Root+"/hit-readback", []byte("real-response-discarded\n"), 0600)
	return true
}
