// Strategy side, Go.
package main

import (
	"bufio"
	"encoding/binary"
	"io"
	"math"
	"net"
	"os"
)

func main() {
	c, err := net.Dial("unix", os.Args[1])
	if err != nil {
		panic(err)
	}
	r := bufio.NewReaderSize(c, 65536)
	var ema [4096]float64
	h := make([]byte, 4)
	body := make([]byte, 1<<20)
	out := make([]byte, 6)
	for {
		if _, err := io.ReadFull(r, h); err != nil {
			return
		}
		n := binary.LittleEndian.Uint32(h)
		b := body[:n]
		io.ReadFull(r, b)
		cnt := int(binary.LittleEndian.Uint16(b[1:]))
		k := 0
		for i := 0; i < cnt; i++ {
			o := 3 + 32*i
			id := binary.LittleEndian.Uint32(b[o:])
			px := math.Float64frombits(binary.LittleEndian.Uint64(b[o+8:]))
			e := ema[id&4095]
			e += 0.1 * (px - e)
			ema[id&4095] = e
			if px > e*1.5 {
				k++
			}
		}
		binary.LittleEndian.PutUint32(out, 2)
		binary.LittleEndian.PutUint16(out[4:], uint16(k))
		c.Write(out)
	}
}
