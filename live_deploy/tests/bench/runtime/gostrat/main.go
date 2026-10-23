// A strategy in Go, run by the Linck runtime prototype (../supervisor.py).
//
// The top half is what the Go SDK would be: it speaks the protocol
// (newline-delimited JSON on a Unix socket here; protobuf in the real
// thing), owns main(), turns every batch into calls on the strategy, saves
// and restores its declared state, and reports a panic with its stack
// instead of dying silently. The bottom half is the only part a user
// writes: a struct, its state, and OnBatch.
package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"runtime/debug"
	"time"
)

// ── the SDK ─────────────────────────────────────────────────────────────

type Tick struct {
	ID int     `json:"id"`
	Px float64 `json:"px"`
}
type Fill struct {
	IntentID string  `json:"intent_id"`
	Side     string  `json:"side"`
	Qty      int     `json:"qty"`
	Px       float64 `json:"px"`
}
type Msg struct {
	Type       string             `json:"type"`
	Seq        int64              `json:"seq"`
	Now        int64              `json:"now"`
	Ticks      []Tick             `json:"ticks"`
	Fills      []Fill             `json:"fills"`
	Params     map[string]float64 `json:"params"`
	State      json.RawMessage    `json:"state"`
	Deployment string             `json:"deployment"`
}
type Command struct {
	IntentID string `json:"intent_id"`
	Side     string `json:"side"`
	Qty      int    `json:"qty"`
	Inst     int    `json:"inst"`
}
type Ctx struct {
	Seq       int64
	Now       int64
	dep       string
	cmds      []Command
	decisions []string
}

// Order: the intent id is the deployment, the batch and the order's place
// in it — the same every time the same batch is replayed, so the order
// service can tell a replay from a new order.
func (c *Ctx) Order(side string, qty, inst int) {
	c.cmds = append(c.cmds, Command{fmt.Sprintf("%s:%d:%d", c.dep, c.Seq, len(c.cmds)), side, qty, inst})
}
func (c *Ctx) Decide(rule, outcome, why string) {
	c.decisions = append(c.decisions, rule+"|"+outcome+"|"+why)
}

func send(w *bufio.Writer, v any) {
	b, _ := json.Marshal(v)
	w.Write(b)
	w.WriteByte('\n')
	w.Flush()
}

func stateHash(s any) string {
	b, _ := json.Marshal(s)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:8])
}

func main() {
	c, err := net.Dial("unix", os.Args[1])
	if err != nil {
		panic(err)
	}
	r := bufio.NewReaderSize(c, 1<<20)
	w := bufio.NewWriter(c)
	st := &Strategy{}
	var dep string
	var cur int64
	defer func() { // a panic in the strategy: say where and why, then exit
		if p := recover(); p != nil {
			send(w, map[string]any{"type": "error", "seq": cur, "error": fmt.Sprint(p), "stack": string(debug.Stack())})
			os.Exit(2)
		}
	}()
	for {
		line, err := r.ReadBytes('\n')
		if err != nil {
			return
		}
		var m Msg
		json.Unmarshal(line, &m)
		switch m.Type {
		case "hello":
			dep = m.Deployment
			st.Init(m.Params)
			if len(m.State) > 0 && string(m.State) != "null" {
				json.Unmarshal(m.State, &st.S) // restore: the declared state only
			}
			send(w, map[string]any{"type": "ready", "sdk": "go", "subscribe": []int{1}})
		case "batch":
			cur = m.Seq
			ctx := &Ctx{Seq: m.Seq, Now: m.Now, dep: dep, cmds: []Command{}, decisions: []string{}} // never null on the wire
			for _, f := range m.Fills {
				st.OnFill(ctx, f)
			}
			st.OnBatch(ctx, m.Ticks)
			send(w, map[string]any{"type": "done", "seq": m.Seq, "commands": ctx.cmds, "decisions": ctx.decisions, "state_hash": stateHash(st.S)})
		case "snapshot":
			send(w, map[string]any{"type": "snapshot", "seq": m.Seq, "state": st.S})
		}
	}
}

// ── the user's strategy ─────────────────────────────────────────────────

type State struct { // everything that decides the future; saved and restored by the SDK
	Fast, Slow float64
	Pos        int
	Seen       int64
}

type Strategy struct {
	S            State
	fastA, slowA float64
	panicAt      int64
	work         time.Duration // a slow strategy, for the latest-tick test
}

func (s *Strategy) Init(p map[string]float64) {
	s.fastA, s.slowA, s.panicAt = p["fast"], p["slow"], int64(p["panic_at"])
	s.work = time.Duration(p["work_ms"] * float64(time.Millisecond))
}

func (s *Strategy) OnFill(ctx *Ctx, f Fill) {
	if f.Side == "buy" {
		s.S.Pos += f.Qty
	} else {
		s.S.Pos -= f.Qty
	}
}

func (s *Strategy) OnBatch(ctx *Ctx, ticks []Tick) {
	if s.panicAt > 0 && ctx.Seq == s.panicAt {
		var legs []int
		_ = legs[3] // a bug: index out of range, on this batch, every time
	}
	if s.work > 0 {
		time.Sleep(s.work)
	}
	for _, t := range ticks {
		if t.ID != 1 {
			continue
		}
		if s.S.Seen == 0 {
			s.S.Fast, s.S.Slow = t.Px, t.Px
		}
		s.S.Seen++
		s.S.Fast += s.fastA * (t.Px - s.S.Fast)
		s.S.Slow += s.slowA * (t.Px - s.S.Slow)
	}
	if s.S.Seen < 20 {
		return
	}
	switch {
	case s.S.Pos == 0 && s.S.Fast > s.S.Slow*1.0004:
		ctx.Order("buy", 75, 1)
		ctx.Decide("entry", "acted", fmt.Sprintf("fast %.4f above slow %.4f", s.S.Fast, s.S.Slow))
	case s.S.Pos > 0 && s.S.Fast < s.S.Slow*0.9996:
		ctx.Order("sell", s.S.Pos, 1)
		ctx.Decide("exit", "acted", fmt.Sprintf("fast %.4f below slow %.4f", s.S.Fast, s.S.Slow))
	}
}
