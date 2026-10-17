//go:build linux && !android

package internal

// Copied into the disposable LAB image only. No listener in the runtime patch.
import (
	"bufio"
	"encoding/json"
	"errors"
	"net"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/netbirdio/netbird/client/firewall/binat"
	"github.com/netbirdio/netbird/client/internal/acl"
	mgm "github.com/netbirdio/netbird/shared/management/proto"
	"google.golang.org/protobuf/proto"
)

type binatLabState struct {
	sync.Mutex
	mode        string
	hold        bool
	paused      bool
	resume      chan struct{}
	maps        uint64
	rejected    uint64
	resetDone   bool
	resetFailed bool
}

func (e *Engine) startBINATLab() error {
	if e.binatGate == nil {
		return nil
	}
	if os.Getenv("OPENMAIN_ISOLATED_TEST") != "1" || os.Getenv("OPENMAIN_LAB_INJECTORS") != "1" {
		return errors.New("lab instrumented client refuses non-lab execution")
	}
	e.binatLab = &binatLabState{}
	path := binat.Root + "/inject-" + strconv.Itoa(os.Getpid()) + ".sock"
	// No replacing a socket belonging to another owner/process.
	listener, err := net.Listen("unix", path)
	if err != nil {
		return errors.New("lab injector socket unavailable")
	}
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		return err
	}
	go func() { <-e.ctx.Done(); listener.Close() }()
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			conn.SetDeadline(time.Now().Add(10 * time.Second))
			scanner := bufio.NewScanner(conn)
			scanner.Buffer(make([]byte, 256), 256)
			if scanner.Scan() {
				var request struct {
					Operation string `json:"operation"`
				}
				if json.Unmarshal(scanner.Bytes(), &request) == nil {
					json.NewEncoder(conn).Encode(e.binatLabCommand(request.Operation))
				}
			}
			conn.Close()
		}
	}()
	return nil
}

func (e *Engine) binatLabCommand(op string) map[string]any {
	s := e.binatLab
	s.Lock()
	defer s.Unlock()
	switch op {
	case "status":
	case "stale", "invalid", "unknown", "incomplete", "pause", "ack", "readback":
		if s.mode != "" || s.hold || s.paused {
			return map[string]any{"ok": false}
		}
		s.mode = op
	case "release":
		s.hold = false
		if s.resume != nil {
			close(s.resume)
			s.resume = nil
		}
	case "unpause":
		if s.resume == nil {
			return map[string]any{"ok": false}
		}
		close(s.resume)
		s.resume = nil
	case "reset":
		// Invalidate the pending publication BEFORE waiting on Engine.syncMsgMux.
		// This models reset/disconnect arriving while apply owns the Engine lock.
		s.hold = true
		s.resetDone, s.resetFailed = false, false
		if e.binatGate.Close() != nil {
			return map[string]any{"ok": false}
		}
		go func() {
			e.syncMsgMux.Lock()
			defer e.syncMsgMux.Unlock()
			err := e.firewall.Close(e.stateManager)
			if err == nil {
				err = e.createFirewall()
			}
			if err == nil {
				e.acl = acl.NewDefaultManagerForBINAT(e.firewall)
				e.dnsServer.SetFirewall(e.firewall)
			}
			s.Lock()
			s.resetDone, s.resetFailed = true, err != nil
			s.Unlock()
		}()
	default:
		return map[string]any{"ok": false}
	}
	return map[string]any{"ok": true, "maps": s.maps, "rejected": s.rejected,
		"hold": s.hold, "paused": s.paused, "reset_done": s.resetDone, "reset_failed": s.resetFailed}
}

func (e *Engine) binatLabMap(m *mgm.NetworkMap) (*mgm.NetworkMap, error) {
	s := e.binatLab
	if s == nil {
		return m, nil
	}
	s.Lock()
	defer s.Unlock()
	s.maps++
	if s.hold {
		s.rejected++
		return nil, errors.New("lab complete map withheld")
	}
	mode := s.mode
	if mode == "" || mode == "pause" {
		return m, nil
	}
	s.mode, s.hold = "", true
	s.rejected++
	if m == nil {
		return nil, errors.New("lab map absent")
	}
	copy := proto.Clone(m).(*mgm.NetworkMap)
	switch mode {
	case "ack", "readback":
		f, err := os.OpenFile(binat.Root+"/fault-"+mode, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return nil, errors.New("lab I/O fixture unavailable")
		}
		f.Close()
	case "stale":
		if e.networkSerial == 0 {
			return nil, errors.New("lab stale fixture has no baseline")
		}
		copy.Serial = e.networkSerial - 1
	case "incomplete":
		copy.RoutesFirewallRules = nil
		copy.RoutesFirewallRulesIsEmpty = false
	case "invalid", "unknown":
		if len(copy.RoutesFirewallRules) == 0 {
			return nil, errors.New("lab route fixture missing")
		}
		if mode == "invalid" {
			copy.RoutesFirewallRules[0].Protocol = mgm.RuleProtocol(999)
		} else {
			copy.RoutesFirewallRules[0].ProtoReflect().SetUnknown([]byte{0xf8, 0x7f, 0x01})
		}
	}
	return copy, nil
}

func (e *Engine) binatLabBeforeOpen() error {
	s := e.binatLab
	if s == nil {
		return nil
	}
	s.Lock()
	if s.mode != "pause" {
		s.Unlock()
		return nil
	}
	s.mode, s.paused = "", true
	resume := make(chan struct{})
	s.resume = resume
	s.Unlock()
	defer func() { s.Lock(); s.paused = false; s.Unlock() }()
	select {
	case <-resume:
		return nil
	case <-e.ctx.Done():
		return e.ctx.Err()
	case <-time.After(45 * time.Second):
		return errors.New("lab publication barrier expired")
	}
}
