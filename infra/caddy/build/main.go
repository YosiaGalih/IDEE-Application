// The Caddy binary of kodekantor/caddy: the official entry point (caddy v2.11.7 cmd/caddy/main.go) with the standard
// modules, built from source so the Go toolchain and golang.org/x/net carry their security fixes (DECISIONS EX-03).
package main

import (
	_ "time/tzdata"

	caddycmd "github.com/caddyserver/caddy/v2/cmd"

	// plug in Caddy modules here
	_ "github.com/caddyserver/caddy/v2/modules/standard"
)

func main() {
	caddycmd.Main()
}
