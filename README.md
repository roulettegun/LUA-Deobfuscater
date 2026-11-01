# Lua / Luau Deobfuscator
`lua_deobfuscator.py` it was mainly made for LUAU since most of the skid make script with key and post them saying it was keyless. it also statically unpacks, decrypts, and constant-folds obfuscated Lua 5.1 and Luau scripts without running untrusted code in a runtime. It parses input scripts down to raw tokens, strips common packer layers, solves string tables, and prints clean source code.
- Standard library only (`argparse`, `dataclasses`, `re`, `math`, `pathlib`).
- Safe static analysis (no `loadstring`, no subshells, no sandboxes).
- Luau-native support (understands Luau inline `if-then-else` expressions, compound operators, and PCG32 random seeds).
---
## Common Commands
```bash
# Auto detect algoritm
python3 lua_deobfuscator.py input.lua output.lua
# Quick inspection without writing to disk
python3 lua_deobfuscator.py --detect-only input.lua
# Force specific engine or keep raw compact format
python3 lua_deobfuscator.py input.lua output.lua --algo rolling_xor_table
python3 lua_deobfuscator.py input.lua output.lua --no-format
```
---
## Detection & Algorithm Pipeline
Incoming files are fingerprinted by their header layout, control-flow dispatchers, and cipher loops:
| Engine | Target Pattern | What it does |
|---|---|---|
| `loadstring_packer` | `loadstring("...")()`, `string.char(...)` payloads, byte XOR loops | Unwraps `loadstring("...")()` and byte XOR/add/sub loops recursively up to 5 layers deep. |
| `prometheus_vm` | Prometheus Medium / Strong VM presets | Solves custom Base64/ASCII85 Layer-1 tables, extracts the 45-bit LCG + mod 257 PRNG constants, bypasses character shuffling, and annotates VM dispatch leaves. |
| `prometheus_strings` | Prometheus Weak / Constant-Array / `NumbersToExpressions` presets | Solves Layer-1 tables, inlines offsets, strips decoder headers, and folds math for weak/non-VM presets. |
| `rolling_xor_table` | IIFE Rolling-XOR tables (`table.create`, `bit32.bxor`, `(K + i + j) % 256`) | Recovers rolling XOR key `(K + i + j) % 256` (with UTF-8 scoring fallback), prunes honeypot `do ... end` blocks, and inlines string arrays. |
| `seeded_prng_cipher` | Custom pure-Lua LCG loops or `math.randomseed(S)` + `math.random` | Resolves custom pure-Lua LCG ciphers and simulates Luau's bit-exact 64-bit PCG32 engine for `math.randomseed(S)`. |
| `constant_array` | WeAreDevs / basic string array packers | Simulates array queue rotations (`table.insert`/`remove`), resolves index offsets, and inlines strings. |
| `math_expression` | General math, bitwise, string-byte, and escape-sequence obfuscation | Precedence-exact constant folding for `math.*`, `bit32.*`, `string.*`, concatenations, and arithmetic. |
---
## Internal Architecture & Mechanical Flow
```
