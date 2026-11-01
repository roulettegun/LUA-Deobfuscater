#!/usr/bin/env python3
"""Static deobfuscator for Lua 5.1 and Luau scripts."""

from __future__ import annotations

import argparse
import dataclasses
import math
import re
import sys
from pathlib import Path



@dataclasses.dataclass
class Token:
    kind: str   # 'word', 'number', 'string', 'symbol', 'comment'
    val: str
    start: int = 0
    end: int = 0


TWO_CHAR_SYMBOLS = {
    '==', '~=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '^=',
    '//', '..', '::', '->',
}

LUA_KEYWORDS = {
    'and', 'break', 'continue', 'do', 'else', 'elseif', 'end', 'false',
    'for', 'function', 'goto', 'if', 'in', 'local', 'nil', 'not', 'or',
    'repeat', 'return', 'then', 'true', 'until', 'while',
}

EXPR_IF_PREDECESSORS = {
    '=', '==', '~=', '<', '>', '<=', '>=', '+', '-', '*', '/', '//', '%',
    '^', '..', '+=', '-=', '*=', '/=', '%=', '^=', '..=',
    '(', '[', '{', ',', ';',
    'return', 'and', 'or', 'not', 'in',
}


def tokenize_lua(code: str) -> list[Token]:
    """Tokenize Lua / Luau source code while preserving exact character spans."""
    tokens: list[Token] = []
    i = 0
    n = len(code)

    while i < n:
        c = code[i]

        if c.isspace():
            i += 1
            continue

        if c == '-' and i + 1 < n and code[i + 1] == '-':
            start = i
            if i + 2 < n and code[i + 2] == '[':
                eqs = 0
                j = i + 3
                while j < n and code[j] == '=':
                    eqs += 1
                    j += 1
                if j < n and code[j] == '[':
                    tag = ']' + ('=' * eqs) + ']'
                    end_idx = code.find(tag, j + 1)
                    end_pos = (end_idx + len(tag)) if end_idx != -1 else n
                    tokens.append(Token('comment', code[start:end_pos], start, end_pos))
                    i = end_pos
                    continue
            end_idx = code.find('\n', i)
            end_pos = end_idx if end_idx != -1 else n
            tokens.append(Token('comment', code[start:end_pos], start, end_pos))
            i = end_pos
            continue

        if c in ('"', "'"):
            quote = c
            start = i
            i += 1
            while i < n:
                if code[i] == '\\':
                    i += 2
                elif code[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            tokens.append(Token('string', code[start:i], start, i))
            continue

        if c == '[' and i + 1 < n and code[i + 1] in ('[', '='):
            eqs = 0
            j = i + 1
            while j < n and code[j] == '=':
                eqs += 1
                j += 1
            if j < n and code[j] == '[':
                start = i
                tag = ']' + ('=' * eqs) + ']'
                end_idx = code.find(tag, j + 1)
                if end_idx != -1:
                    end_pos = end_idx + len(tag)
                    tokens.append(Token('string', code[start:end_pos], start, end_pos))
                    i = end_pos
                    continue

        if c.isalpha() or c == '_':
            start = i
            i += 1
            while i < n and (code[i].isalnum() or code[i] == '_'):
                i += 1
            tokens.append(Token('word', code[start:i], start, i))
            continue

        if c.isdigit() or (c == '.' and i + 1 < n and code[i + 1].isdigit()):
            start = i
            if c == '0' and i + 1 < n and code[i + 1] in ('x', 'X'):
                i += 2
                while i < n and (code[i] in '0123456789abcdefABCDEF_.'):
                    i += 1
                if i < n and code[i] in ('p', 'P'):
                    i += 1
                    if i < n and code[i] in ('+', '-'):
                        i += 1
                    while i < n and (code[i].isdigit() or code[i] == '_'):
                        i += 1
            elif c == '0' and i + 1 < n and code[i + 1] in ('b', 'B'):
                i += 2
                while i < n and code[i] in ('0', '1', '_'):
                    i += 1
            else:
                while i < n and (code[i].isdigit() or code[i] in ('.', '_')):
                    if code[i] == '.' and i + 1 < n and code[i + 1] == '.':
                        break
                    i += 1
                if i < n and code[i] in ('e', 'E'):
                    i += 1
                    if i < n and code[i] in ('+', '-'):
                        i += 1
                    while i < n and (code[i].isdigit() or code[i] == '_'):
                        i += 1
            tokens.append(Token('number', code[start:i], start, i))
            continue

        if code[i : i + 3] in ('...', '..=', '//='):
            tokens.append(Token('symbol', code[i : i + 3], i, i + 3))
            i += 3
            continue

        if code[i : i + 2] in TWO_CHAR_SYMBOLS:
            tokens.append(Token('symbol', code[i : i + 2], i, i + 2))
            i += 2
            continue

        tokens.append(Token('symbol', c, i, i + 1))
        i += 1

    return tokens


def parse_lua_string_literal(lit: str) -> bytes:
    """Decode any Lua string literal ("...", '...', [[...]], [=[...]=]) into raw bytes."""
    if not lit:
        return b''
    if lit.startswith('['):
        j = 1
        while j < len(lit) and lit[j] == '=':
            j += 1
        if j < len(lit) and lit[j] == '[':
            inner = lit[j + 1 : -(j + 1)]
            if inner.startswith('\r\n'):
                inner = inner[2:]
            elif inner.startswith('\n'):
                inner = inner[1:]
            return inner.encode('utf-8')

    quote = lit[0]
    if quote not in ('"', "'") or len(lit) < 2:
        return lit.encode('utf-8')

    inner = lit[1:-1]
    out = bytearray()
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch == '\\' and i + 1 < n:
            nxt = inner[i + 1]
            if nxt.isdigit():
                digits = nxt
                k = i + 2
                while k < n and inner[k].isdigit() and len(digits) < 3:
                    digits += inner[k]
                    k += 1
                out.append(int(digits) & 0xFF)
                i = k
                continue
            elif nxt == 'x' and i + 3 < n:
                hx = inner[i + 2 : i + 4]
                try:
                    out.append(int(hx, 16))
                    i += 4
                    continue
                except ValueError:
                    pass
            elif nxt == 'u' and i + 3 < n and inner[i + 2] == '{':
                close = inner.find('}', i + 3)
                if close != -1:
                    try:
                        cp = int(inner[i + 3 : close], 16)
                        out.extend(chr(cp).encode('utf-8'))
                        i = close + 1
                        continue
                    except ValueError:
                        pass
            elif nxt == 'z':
                k = i + 2
                while k < n and inner[k].isspace():
                    k += 1
                i = k
                continue
            esc_map = {
                'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11,
                '\\': 92, '"': 34, "'": 39, '\n': 10,
            }
            if nxt in esc_map:
                out.append(esc_map[nxt])
            else:
                out.extend(nxt.encode('utf-8'))
            i += 2
        else:
            # Preserve single-byte latin1 if high-char raw byte, else UTF-8
            cp = ord(ch)
            if cp <= 0xFF:
                out.append(cp)
            else:
                out.extend(ch.encode('utf-8'))
            i += 1
    return bytes(out)


def bytes_to_lua_string(raw: bytes) -> str:
    """Format raw bytes as a clean, valid, readable Lua double-quoted string literal."""
    try:
        text = raw.decode('utf-8')
        buf = ['"']
        for idx, ch in enumerate(text):
            cp = ord(ch)
            if ch == '\\':
                buf.append('\\\\')
            elif ch == '"':
                buf.append('\\"')
            elif ch == '\n':
                buf.append('\\n')
            elif ch == '\r':
                buf.append('\\r')
            elif ch == '\t':
                buf.append('\\t')
            elif cp < 32 or cp == 127:
                nxt_is_digit = (idx + 1 < len(text)) and text[idx + 1].isdigit()
                buf.append(f'\\{cp:03d}' if nxt_is_digit else f'\\{cp}')
            else:
                buf.append(ch)
        buf.append('"')
        return ''.join(buf)
    except UnicodeDecodeError:
        buf = ['"']
        for idx, b in enumerate(raw):
            if b == 92:
                buf.append('\\\\')
            elif b == 34:
                buf.append('\\"')
            elif b == 10:
                buf.append('\\n')
            elif b == 13:
                buf.append('\\r')
            elif b == 9:
                buf.append('\\t')
            elif 32 <= b <= 126:
                buf.append(chr(b))
            else:
                nxt_is_digit = (idx + 1 < len(raw)) and (48 <= raw[idx + 1] <= 57)
                buf.append(f'\\{b:03d}' if nxt_is_digit else f'\\{b}')
        buf.append('"')
        return ''.join(buf)


def str_to_lua_string(s: str) -> str:
    """Format a Python str (utf-8) as a valid Lua string literal."""
    return bytes_to_lua_string(s.encode('utf-8', errors='surrogateescape'))


def parse_lua_num(s: str) -> int | float | None:
    """Parse any Lua / Luau numeric literal string into int or float."""
    clean = s.replace('_', '').strip()
    if not clean:
        return None
    sign = 1
    if clean.startswith('-'):
        sign = -1
        clean = clean[1:].strip()
    elif clean.startswith('+'):
        clean = clean[1:].strip()

    try:
        if clean.startswith(('0x', '0X')):
            if '.' in clean or 'p' in clean or 'P' in clean:
                return sign * float.fromhex(clean)
            return sign * int(clean, 16)
        if clean.startswith(('0b', '0B')):
            return sign * int(clean[2:], 2)
        if '.' in clean or 'e' in clean or 'E' in clean:
            val = sign * float(clean)
            if math.isfinite(val) and val.is_integer() and abs(val) < 1e16:
                return int(val)
            return val
        return sign * int(clean, 10)
    except ValueError:
        return None


def fmt_lua_num(val: int | float) -> str:
    """Format a Python int/float cleanly as a Lua numeric token."""
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, float):
        if not math.isfinite(val):
            return 'math.huge' if val > 0 else '-math.huge'
        if val.is_integer() and abs(val) < 1e16:
            return str(int(val))
        return f'{val:.12g}'
    return str(val)


def is_expr_if(tokens: list[Token], idx: int) -> bool:
    """Return True if `tokens[idx]` ('if') is a Luau inline if-expression (no trailing `end`)."""
    k = idx - 1
    while k >= 0 and tokens[k].kind == 'comment':
        k -= 1
    if k < 0:
        return False
    return tokens[k].val in EXPR_IF_PREDECESSORS


def find_matching_block_end(tokens: list[Token], open_idx: int) -> int:
    """Find token index of matching 'end' for block opened at open_idx."""
    i = open_idx
    if tokens[i].val in ('while', 'for'):
        paren = 0
        while i < len(tokens):
            if tokens[i].val == '(':
                paren += 1
            elif tokens[i].val == ')':
                paren = max(0, paren - 1)
            elif tokens[i].val == 'do' and paren == 0:
                break
            i += 1
        if i >= len(tokens):
            return -1

    stack: list[str] = []
    if_expr_depth = 0
    just_closed_expr_if = False

    while i < len(tokens):
        t = tokens[i]
        if t.kind == 'comment':
            i += 1
            continue
        if t.kind == 'word':
            if t.val == 'if':
                k = i - 1
                while k >= 0 and tokens[k].kind == 'comment':
                    k -= 1
                pv = tokens[k].val if k >= 0 else None
                if (
                    pv in EXPR_IF_PREDECESSORS
                    or (if_expr_depth > 0 and pv in ('then', 'elseif'))
                    or (just_closed_expr_if and pv == 'else')
                ):
                    if_expr_depth += 1
                else:
                    stack.append('if')
                just_closed_expr_if = False
            elif t.val == 'else' and if_expr_depth > 0:
                if_expr_depth -= 1
                just_closed_expr_if = True
            elif t.val in ('do', 'function'):
                stack.append(t.val)
                just_closed_expr_if = False
            elif t.val == 'end':
                just_closed_expr_if = False
                if stack:
                    stack.pop()
                    if not stack:
                        return i
            else:
                just_closed_expr_if = False
        else:
            just_closed_expr_if = False
        i += 1
    return -1



class LuauPCG32:
    """Bit-exact implementation of Luau's `math.randomseed` & `math.random` (PCG32)."""

    PCG32_INC = 105
    MASK64 = 0xFFFFFFFFFFFFFFFF
    MASK32 = 0xFFFFFFFF
    MULT = 6364136223846793005

    def __init__(self, seed: int = 0):
        self.state = 0
        self.seed(seed)

    def next_u32(self) -> int:
        old = self.state
        self.state = (old * self.MULT + (self.PCG32_INC | 1)) & self.MASK64
        xorshifted = (((old >> 18) ^ old) >> 27) & self.MASK32
        rot = (old >> 59) & 31
        return ((xorshifted >> rot) | (xorshifted << ((-rot) & 31))) & self.MASK32

    def seed(self, s: int) -> None:
        self.state = 0
        self.next_u32()
        # Luau casts integer seed to uint64_t (from int32_t / int)
        s_i32 = int(s) & self.MASK32
        if s_i32 & 0x80000000:
            s_u64 = (s_i32 - 0x100000000) & self.MASK64
        else:
            s_u64 = s_i32
        self.state = (self.state + s_u64) & self.MASK64
        self.next_u32()

    def random(self, *args: int) -> int | float:
        if len(args) == 0:
            rl = self.next_u32()
            rh = self.next_u32()
            return math.ldexp(float(rl | (rh << 32)), -64)
        elif len(args) == 1:
            l, u = 1, int(args[0])
        else:
            l, u = int(args[0]), int(args[1])
        if l > u:
            raise ValueError("interval is empty")
        ul = (u - l) & self.MASK32
        if ul == self.MASK32:
            return l + self.next_u32()
        x = (ul + 1) * self.next_u32()
        return l + (x >> 32)


BIN_PRECEDENCE: dict[str, tuple[int, str]] = {
    '==': (3, 'L'), '~=': (3, 'L'), '<': (3, 'L'), '>': (3, 'L'), '<=': (3, 'L'), '>=': (3, 'L'),
    '..': (5, 'R'),
    '+': (6, 'L'), '-': (6, 'L'),
    '*': (7, 'L'), '/': (7, 'L'), '//': (7, 'L'), '%': (7, 'L'),
    '^': (9, 'R'),
}
UNARY_PREC = 8

UNARY_PREV_VALS = {
    '(', '[', '{', ',', ';', '=', '==', '~=', '<', '>', '<=', '>=',
    '+', '-', '*', '/', '//', '%', '^', '..', '+=', '-=', '*=', '/=', '%=', '^=', '..=',
    'return', 'if', 'elseif', 'while', 'until', 'and', 'or', 'not', 'then', 'else', 'do', 'in',
}


def _tok_const_val(t: Token):
    """Extract Python value ('num', val) | ('str', bytes) | ('bool', bool) from a literal token."""
    if t.kind == 'number':
        v = parse_lua_num(t.val)
        if v is not None:
            return ('num', v)
    elif t.kind == 'string':
        return ('str', parse_lua_string_literal(t.val))
    elif t.kind == 'word':
        if t.val == 'true':
            return ('bool', True)
        if t.val == 'false':
            return ('bool', False)
    return None


def _val_to_token(v_tuple) -> Token | None:
    kind, val = v_tuple
    if kind == 'num':
        if isinstance(val, float) and not math.isfinite(val):
            return None
        return Token('number', fmt_lua_num(val))
    if kind == 'str':
        return Token('string', bytes_to_lua_string(val))
    if kind == 'bool':
        return Token('word', 'true' if val else 'false')
    return None


def _eval_stdlib_call(lib: str, func: str, args: list[tuple[str, object]], rng: LuauPCG32 | None = None):
    """Evaluate pure `math.*`, `bit32.*` / `bit.*`, or `string.*` calls on constant arguments."""
    try:
        if lib == 'math':
            if func == 'randomseed' and rng is not None and len(args) == 1 and args[0][0] == 'num':
                rng.seed(int(args[0][1]))
                return None
            if func == 'random':
                if any(a[0] != 'num' for a in args):
                    return None
                nums = [int(a[1]) for a in args]
                if len(nums) == 1 and nums[0] == 1:
                    return ('num', 1)
                if len(nums) == 2 and nums[0] == nums[1]:
                    return ('num', nums[0])
                if rng is not None and len(nums) in (0, 1, 2):
                    return ('num', rng.random(*nums))
                return None

            if any(a[0] != 'num' for a in args):
                return None
            nums_f = [float(a[1]) for a in args]
            if func == 'floor' and len(nums_f) == 1:
                return ('num', math.floor(nums_f[0]))
            if func == 'ceil' and len(nums_f) == 1:
                return ('num', math.ceil(nums_f[0]))
            if func == 'abs' and len(nums_f) == 1:
                return ('num', abs(args[0][1]))
            if func == 'sqrt' and len(nums_f) == 1 and nums_f[0] >= 0:
                return ('num', math.sqrt(nums_f[0]))
            if func == 'pow' and len(nums_f) == 2:
                return ('num', math.pow(nums_f[0], nums_f[1]))
            if func == 'fmod' and len(nums_f) == 2 and nums_f[1] != 0:
                return ('num', math.fmod(nums_f[0], nums_f[1]))
            if func == 'ldexp' and len(nums_f) == 2 and abs(nums_f[1]) < 1024:
                return ('num', math.ldexp(nums_f[0], int(nums_f[1])))
            if func == 'max' and len(nums_f) >= 1:
                return ('num', max(a[1] for a in args))
            if func == 'min' and len(nums_f) >= 1:
                return ('num', min(a[1] for a in args))
            if func == 'deg' and len(nums_f) == 1:
                return ('num', math.degrees(nums_f[0]))
            if func == 'rad' and len(nums_f) == 1:
                return ('num', math.radians(nums_f[0]))
            if func == 'sin' and len(nums_f) == 1:
                return ('num', math.sin(nums_f[0]))
            if func == 'cos' and len(nums_f) == 1:
                return ('num', math.cos(nums_f[0]))
            if func == 'tan' and len(nums_f) == 1:
                return ('num', math.tan(nums_f[0]))
            if func == 'asin' and len(nums_f) == 1 and -1 <= nums_f[0] <= 1:
                return ('num', math.asin(nums_f[0]))
            if func == 'acos' and len(nums_f) == 1 and -1 <= nums_f[0] <= 1:
                return ('num', math.acos(nums_f[0]))
            if func == 'atan' and len(nums_f) in (1, 2):
                return ('num', math.atan(nums_f[0]) if len(nums_f) == 1 else math.atan2(nums_f[0], nums_f[1]))
            if func == 'atan2' and len(nums_f) == 2:
                return ('num', math.atan2(nums_f[0], nums_f[1]))
            if func == 'sinh' and len(nums_f) == 1 and abs(nums_f[0]) < 700:
                return ('num', math.sinh(nums_f[0]))
            if func == 'cosh' and len(nums_f) == 1 and abs(nums_f[0]) < 700:
                return ('num', math.cosh(nums_f[0]))
            if func == 'tanh' and len(nums_f) == 1:
                return ('num', math.tanh(nums_f[0]))
            if func == 'exp' and len(nums_f) == 1 and nums_f[0] < 700:
                return ('num', math.exp(nums_f[0]))
            if func == 'log' and len(nums_f) in (1, 2) and nums_f[0] > 0:
                return ('num', math.log(nums_f[0]) if len(nums_f) == 1 else math.log(nums_f[0], nums_f[1]))
            if func == 'log10' and len(nums_f) == 1 and nums_f[0] > 0:
                return ('num', math.log10(nums_f[0]))
            if func == 'clamp' and len(nums_f) == 3:
                x, lo, hi = args[0][1], args[1][1], args[2][1]
                return ('num', max(lo, min(hi, x)))
            if func == 'sign' and len(nums_f) == 1:
                return ('num', 1 if nums_f[0] > 0 else (-1 if nums_f[0] < 0 else 0))
            if func == 'round' and len(nums_f) == 1:
                x = nums_f[0]
                return ('num', math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5))

        elif lib in ('bit32', 'bit'):
            if any(a[0] != 'num' for a in args):
                return None
            u32 = [int(a[1]) & 0xFFFFFFFF for a in args]
            if func == 'bxor':
                res = 0
                for v in u32:
                    res ^= v
                return ('num', res)
            if func == 'band':
                res = 0xFFFFFFFF
                for v in u32:
                    res &= v
                return ('num', res)
            if func == 'bor':
                res = 0
                for v in u32:
                    res |= v
                return ('num', res)
            if func == 'btest':
                res = 0xFFFFFFFF
                for v in u32:
                    res &= v
                return ('bool', res != 0)
            if func == 'bnot' and len(u32) == 1:
                res = (~u32[0]) & 0xFFFFFFFF
                if lib == 'bit' and res & 0x80000000:
                    res -= 0x100000000
                return ('num', res)
            if func == 'lshift' and len(u32) == 2:
                disp = int(args[1][1])
                if lib == 'bit':
                    disp &= 31
                res = ((u32[0] << disp) & 0xFFFFFFFF) if 0 <= disp < 32 else (
                    (u32[0] >> (-disp)) if -32 < disp < 0 else 0
                )
                return ('num', res)
            if func == 'rshift' and len(u32) == 2:
                disp = int(args[1][1])
                if lib == 'bit':
                    disp &= 31
                res = (u32[0] >> disp) if 0 <= disp < 32 else (
                    ((u32[0] << (-disp)) & 0xFFFFFFFF) if -32 < disp < 0 else 0
                )
                return ('num', res)
            if func == 'arshift' and len(u32) == 2:
                disp = int(args[1][1])
                val = u32[0] - 0x100000000 if (u32[0] & 0x80000000) else u32[0]
                if disp >= 32:
                    res = 0xFFFFFFFF if val < 0 else 0
                elif disp <= -32:
                    res = 0
                elif disp >= 0:
                    res = (val >> disp) & 0xFFFFFFFF
                else:
                    res = (val << (-disp)) & 0xFFFFFFFF
                return ('num', res)
            if func in ('lrotate', 'rol') and len(u32) == 2:
                disp = u32[1] & 31
                return ('num', ((u32[0] << disp) | (u32[0] >> ((32 - disp) & 31))) & 0xFFFFFFFF)
            if func in ('rrotate', 'ror') and len(u32) == 2:
                disp = u32[1] & 31
                return ('num', ((u32[0] >> disp) | (u32[0] << ((32 - disp) & 31))) & 0xFFFFFFFF)
            if func == 'extract' and len(u32) in (2, 3):
                field = int(args[1][1])
                width = int(args[2][1]) if len(u32) == 3 else 1
                if 0 <= field < 32 and 1 <= width <= 32 - field:
                    return ('num', (u32[0] >> field) & ((1 << width) - 1))
            if func == 'replace' and len(u32) in (3, 4):
                field = int(args[2][1])
                width = int(args[3][1]) if len(u32) == 4 else 1
                if 0 <= field < 32 and 1 <= width <= 32 - field:
                    mask = ((1 << width) - 1) << field
                    return ('num', (u32[0] & (~mask & 0xFFFFFFFF)) | ((u32[1] << field) & mask))

        elif lib == 'string':
            if func == 'char':
                if any(a[0] != 'num' for a in args):
                    return None
                vals = [int(a[1]) for a in args]
                if all(0 <= v <= 255 for v in vals):
                    return ('str', bytes(vals))
            if func == 'byte' and len(args) in (1, 2) and args[0][0] == 'str':
                b = args[0][1]
                idx = int(args[1][1]) if (len(args) == 2 and args[1][0] == 'num') else 1
                if idx < 0:
                    idx = len(b) + 1 + idx
                if 1 <= idx <= len(b):
                    return ('num', b[idx - 1])
            if func == 'len' and len(args) == 1 and args[0][0] == 'str':
                return ('num', len(args[0][1]))
            if func == 'reverse' and len(args) == 1 and args[0][0] == 'str':
                return ('str', args[0][1][::-1])
            if func == 'lower' and len(args) == 1 and args[0][0] == 'str':
                return ('str', args[0][1].lower())
            if func == 'upper' and len(args) == 1 and args[0][0] == 'str':
                return ('str', args[0][1].upper())
            if func == 'sub' and len(args) in (2, 3) and args[0][0] == 'str':
                if any(a[0] != 'num' for a in args[1:]):
                    return None
                b = args[0][1]
                ln = len(b)
                s_i = int(args[1][1])
                e_i = int(args[2][1]) if len(args) == 3 else ln
                if s_i < 0:
                    s_i = max(1, ln + 1 + s_i)
                elif s_i < 1:
                    s_i = 1
                if e_i < 0:
                    e_i = ln + 1 + e_i
                if e_i > ln:
                    e_i = ln
                return ('str', b[s_i - 1 : e_i] if s_i <= e_i else b'')
            if func == 'rep' and len(args) in (2, 3) and args[0][0] == 'str' and args[1][0] == 'num':
                cnt = int(args[1][1])
                sep = args[2][1] if (len(args) == 3 and args[2][0] == 'str') else b''
                if 0 <= cnt <= 256 and len(args[0][1]) * max(1, cnt) <= 4096:
                    return ('str', sep.join([args[0][1]] * cnt) if cnt > 0 else b'')
    except Exception:
        return None
    return None


def _reduce_flat_segment(seg: list[Token]) -> list[Token]:
    """Fold unary and binary operations inside a flat token list."""
    if len(seg) < 2:
        return seg

    out = list(seg)

    i = 0
    while i + 2 < len(out):
        if out[i].val == 'math' and out[i + 1].val == '.' and out[i + 2].val == 'pi':
            prev_v = out[i - 1].val if i > 0 else None
            if prev_v not in ('.', ':'):
                out[i : i + 3] = [Token('number', repr(math.pi))]
                continue
        i += 1

    changed = True
    while changed:
        changed = False

        idx = len(out) - 2
        while idx >= 1:
            if out[idx].val == '^':
                va = _tok_const_val(out[idx - 1])
                vb = _tok_const_val(out[idx + 1])
                if va and vb and va[0] == 'num' and vb[0] == 'num':
                    after_v = out[idx + 2].val if idx + 2 < len(out) else None
                    if after_v not in ('^', '.', ':', '[', '(') and abs(vb[1]) <= 1024:
                        try:
                            res_tok = _val_to_token(('num', va[1] ** vb[1]))
                            if res_tok:
                                out[idx - 1 : idx + 2] = [res_tok]
                                changed = True
                                idx = min(idx, len(out) - 2)
                                continue
                        except Exception:
                            pass
            idx -= 1

        if changed:
            continue

        idx = len(out) - 2
        while idx >= 0:
            op = out[idx].val
            if op in ('-', '#', 'not'):
                prev_tok = out[idx - 1] if idx > 0 else None
                is_unary = (
                    op in ('#', 'not')
                    or prev_tok is None
                    or prev_tok.val in UNARY_PREV_VALS
                )
                after_v = out[idx + 2].val if idx + 2 < len(out) else None
                if is_unary and after_v not in ('^', '.', ':', '[', '('):
                    va = _tok_const_val(out[idx + 1])
                    if va:
                        res = None
                        if op == '-' and va[0] == 'num':
                            res = ('num', -va[1])
                        elif op == '#' and va[0] == 'str':
                            res = ('num', len(va[1]))
                        elif op == 'not' and va[0] == 'bool':
                            res = ('bool', not va[1])
                        if res:
                            rtok = _val_to_token(res)
                            if rtok:
                                out[idx : idx + 2] = [rtok]
                                changed = True
                                idx = min(idx, len(out) - 2)
                                continue
            idx -= 1

        if changed:
            continue

        for target_prec, assoc in ((7, 'L'), (6, 'L'), (5, 'R'), (3, 'L')):
            indices = range(1, len(out) - 1) if assoc == 'L' else range(len(out) - 2, 0, -1)
            for k in indices:
                op = out[k].val
                info = BIN_PRECEDENCE.get(op)
                if not info or info[0] != target_prec:
                    continue
                if op == '-' and k - 1 == 0:
                    pass
                prev_before_a = out[k - 2] if k >= 2 else None
                after_b = out[k + 2] if k + 2 < len(out) else None

                if prev_before_a is not None:
                    p_val = prev_before_a.val
                    if p_val in ('.', ':'):
                        continue
                    if p_val in ('#', 'not') and target_prec <= UNARY_PREC:
                        continue
                    if p_val == '-':
                        pp = out[k - 3] if k >= 3 else None
                        if (pp is None or pp.val in UNARY_PREV_VALS) and target_prec <= UNARY_PREC:
                            continue
                    p_info = BIN_PRECEDENCE.get(p_val)
                    if p_info:
                        if assoc == 'L' and p_info[0] >= target_prec:
                            continue
                        if assoc == 'R' and p_info[0] > target_prec:
                            continue

                if after_b is not None:
                    a_val = after_b.val
                    if a_val in ('.', ':', '[', '(') or (after_b.kind == 'string'):
                        continue
                    a_info = BIN_PRECEDENCE.get(a_val)
                    if a_info:
                        if assoc == 'L' and a_info[0] > target_prec:
                            continue
                        if assoc == 'R' and a_info[0] >= target_prec:
                            continue

                va = _tok_const_val(out[k - 1])
                vb = _tok_const_val(out[k + 1])
                if not va or not vb:
                    continue

                res = None
                try:
                    if target_prec in (7, 6) and va[0] == 'num' and vb[0] == 'num':
                        a, b = va[1], vb[1]
                        if op == '*':
                            res = ('num', a * b)
                        elif op == '/' and b != 0:
                            res = ('num', a / b)
                        elif op == '//' and b != 0:
                            res = ('num', a // b)
                        elif op == '%' and b != 0:
                            res = ('num', a % b)
                        elif op == '+':
                            res = ('num', a + b)
                        elif op == '-':
                            res = ('num', a - b)
                    elif op == '..' and va[0] in ('str', 'num') and vb[0] in ('str', 'num'):
                        ba = va[1] if va[0] == 'str' else fmt_lua_num(va[1]).encode('utf-8')
                        bb = vb[1] if vb[0] == 'str' else fmt_lua_num(vb[1]).encode('utf-8')
                        if len(ba) + len(bb) <= 65536:
                            res = ('str', ba + bb)
                    elif target_prec == 3:
                        if va[0] == vb[0] and va[0] in ('num', 'str'):
                            a, b = va[1], vb[1]
                            if op == '<':
                                res = ('bool', a < b)
                            elif op == '>':
                                res = ('bool', a > b)
                            elif op == '<=':
                                res = ('bool', a <= b)
                            elif op == '>=':
                                res = ('bool', a >= b)
                            elif op == '==':
                                res = ('bool', a == b)
                            elif op == '~=':
                                res = ('bool', a != b)
                        elif op in ('==', '~='):
                            eq = (va[0] == vb[0]) and (va[1] == vb[1])
                            res = ('bool', eq if op == '==' else not eq)
                except Exception:
                    res = None

                if res is not None:
                    rtok = _val_to_token(res)
                    if rtok:
                        out[k - 1 : k + 2] = [rtok]
                        changed = True
                        break
            if changed:
                break

    return out


def fold_tokens(
    tokens: list[Token],
    unescape_strings: bool = False,
    simulate_seeded_rng: bool = False,
) -> list[Token]:
    """Fold constant expressions and stdlib calls across a token stream."""
    rng = LuauPCG32(0) if simulate_seeded_rng else None
    rng_seeded = False

    def split_args(inner: list[Token]) -> list[list[Token]]:
        if not inner:
            return []
        args: list[list[Token]] = []
        cur: list[Token] = []
        depth = 0
        for tk in inner:
            if tk.val in ('(', '[', '{'):
                depth += 1
                cur.append(tk)
            elif tk.val in (')', ']', '}'):
                depth = max(0, depth - 1)
                cur.append(tk)
            elif tk.val == ',' and depth == 0:
                args.append(cur)
                cur = []
            else:
                cur.append(tk)
        args.append(cur)
        return args

    stack: list[tuple[Token | None, list[Token]]] = [(None, [])]

    idx = 0
    n = len(tokens)
    while idx < n:
        tk = tokens[idx]
        if tk.kind == 'comment':
            stack[-1][1].append(tk)
            idx += 1
            continue

        if unescape_strings and tk.kind == 'string' and tk.val[0] in ('"', "'"):
            if '\\' in tk.val:
                tk = Token('string', bytes_to_lua_string(parse_lua_string_literal(tk.val)), tk.start, tk.end)

        if tk.val in ('(', '[', '{'):
            stack.append((tk, []))
            idx += 1
            continue

        if tk.val in (')', ']', '}') and len(stack) > 1:
            open_tk, inner = stack.pop()
            parent = stack[-1][1]
            next_tk = tokens[idx + 1] if idx + 1 < n else None

            if tk.val == ')' and open_tk and open_tk.val == '(':
                raw_args = split_args([t for t in inner if t.kind != 'comment'])
                comments_in = [t for t in inner if t.kind == 'comment']
                folded_args = [_reduce_flat_segment(a) for a in raw_args] if not comments_in else None

                if (
                    folded_args is not None
                    and len(parent) >= 3
                    and parent[-2].val == '.'
                    and parent[-3].val in ('math', 'bit32', 'bit', 'string')
                    and parent[-1].kind == 'word'
                    and (len(parent) < 4 or parent[-4].val not in ('.', ':', 'function'))
                ):
                    lib = parent[-3].val
                    func = parent[-1].val
                    if all(len(a) == 1 for a in folded_args):
                        cvals = [_tok_const_val(a[0]) for a in folded_args]
                        if all(cv is not None for cv in cvals):
                            if lib == 'math' and func == 'randomseed' and rng is not None and len(cvals) == 1 and cvals[0][0] == 'num':
                                rng.seed(int(cvals[0][1]))
                                rng_seeded = True
                            eval_res = _eval_stdlib_call(
                                lib,
                                func,
                                cvals,  # type: ignore[arg-type]
                                rng if (rng is not None and rng_seeded) else None,
                            )
                            if eval_res is not None:
                                rtok = _val_to_token(eval_res)
                                if rtok is not None:
                                    parent.pop()  # func
                                    parent.pop()  # .
                                    parent.pop()  # lib
                                    parent.append(rtok)
                                    idx += 1
                                    continue

                if folded_args is not None:
                    rebuilt: list[Token] = []
                    for a_i, fa in enumerate(folded_args):
                        if a_i > 0:
                            rebuilt.append(Token('symbol', ','))
                        rebuilt.extend(fa)
                else:
                    rebuilt = _reduce_flat_segment(inner)

                prev_parent = parent[-1] if parent else None
                is_call_or_def_parens = (
                    prev_parent is not None
                    and (
                        (prev_parent.kind == 'word' and prev_parent.val not in LUA_KEYWORDS)
                        or prev_parent.val in (')', ']', '}', 'function')
                    )
                )
                next_chains = next_tk is not None and next_tk.val in ('.', ':', '[', '(')
                if (
                    not is_call_or_def_parens
                    and not next_chains
                    and len(rebuilt) == 1
                    and _tok_const_val(rebuilt[0]) is not None
                ):
                    parent.append(rebuilt[0])
                else:
                    parent.append(open_tk)
                    parent.extend(rebuilt)
                    parent.append(tk)
                idx += 1
                continue

            else:
                folded_inner: list[Token] = []
                chunk: list[Token] = []
                for it in inner:
                    if it.val in (',', ';'):
                        folded_inner.extend(_reduce_flat_segment(chunk))
                        folded_inner.append(it)
                        chunk = []
                    else:
                        chunk.append(it)
                folded_inner.extend(_reduce_flat_segment(chunk))
                parent.append(open_tk)  # type: ignore[arg-type]
                parent.extend(folded_inner)
                parent.append(tk)
                idx += 1
                continue

        stack[-1][1].append(tk)
        idx += 1

    while len(stack) > 1:
        open_tk, inner = stack.pop()
        if open_tk:
            stack[-1][1].append(open_tk)
        stack[-1][1].extend(inner)

    top_tokens = stack[0][1]
    final_tokens: list[Token] = []
    stmt_chunk: list[Token] = []
    STMT_SPLITTERS = {
        'local', 'return', 'if', 'then', 'else', 'elseif', 'while', 'do',
        'for', 'in', 'repeat', 'until', 'function', 'end', 'break', 'continue', ';', ',', '=',
    }
    for tk in top_tokens:
        if tk.kind == 'comment' or tk.val in STMT_SPLITTERS:
            final_tokens.extend(_reduce_flat_segment(stmt_chunk))
            stmt_chunk = []
            final_tokens.append(tk)
        else:
            stmt_chunk.append(tk)
    final_tokens.extend(_reduce_flat_segment(stmt_chunk))
    return final_tokens



def _needs_space_between(prev: Token, cur: Token) -> bool:
    pv, cv = prev.val, cur.val
    if prev.kind == 'comment':
        return True
    # Prevent `-` followed by `-123` from forming `--123` (a Lua comment!)
    if pv == '-' and cv.startswith('-'):
        return True
    if pv in ('(', '[', '{', '.', ':'):
        return False
    if cv in (')', ']', '}', ',', ';', '.', ':'):
        return False
    if pv == '#' and cur.kind in ('word', 'string') or (pv == '#' and cv in ('(', '{')):
        return False
    if cv == '(' and (
        (prev.kind == 'word' and pv not in LUA_KEYWORDS)
        or pv in (')', ']', '}', 'function')
    ):
        return False
    if cv == '[' and (
        (prev.kind == 'word' and pv not in LUA_KEYWORDS)
        or pv in (')', ']', '}')
    ):
        return False
    return True


def tokens_to_compact_lua(tokens: list[Token]) -> str:
    """Serialize tokens back to valid single-space-separated Lua without line reformatting."""
    parts: list[str] = []
    prev: Token | None = None
    for tk in tokens:
        if tk.kind == 'comment':
            if parts and not parts[-1].endswith('\n'):
                parts.append(' ')
            parts.append(tk.val)
            if not tk.val.startswith('--['):
                parts.append('\n')
            prev = tk
            continue
        if prev is not None and _needs_space_between(prev, tk):
            parts.append(' ')
        parts.append(tk.val)
        prev = tk
    return ''.join(parts)


def pretty_print_lua(tokens: list[Token]) -> str:
    """Format a Lua/Luau token stream with standard indentation."""
    out_lines: list[str] = []
    cur_line: list[Token] = []
    indent_level = 0

    def indent_str() -> str:
        return '    ' * max(0, indent_level)

    def flush() -> None:
        nonlocal cur_line
        if not cur_line:
            return
        buf: list[str] = []
        for j, tk in enumerate(cur_line):
            if j > 0 and _needs_space_between(cur_line[j - 1], tk):
                buf.append(' ')
            buf.append(tk.val)
        out_lines.append(indent_str() + ''.join(buf))
        cur_line = []

    STMT_STARTERS = {'local', 'return', 'break', 'continue', 'while', 'for', 'repeat', 'goto'}
    scope_stack = [{'type': 'root', 'paren': 0, 'bracket': 0, 'brace': 0}]
    if_expr_depth = 0
    just_closed_expr_if = False

    i = 0
    n = len(tokens)
    while i < n:
        tk = tokens[i]
        val = tk.val
        prev_tk = tokens[i - 1] if i > 0 else None
        next_tk = tokens[i + 1] if i + 1 < n else None
        prev_val = prev_tk.val if prev_tk else None
        next_val = next_tk.val if next_tk else None

        if tk.kind == 'comment':
            if cur_line and not val.startswith('--['):
                cur_line.append(tk)
                flush()
            else:
                flush()
                out_lines.append(indent_str() + val)
            i += 1
            continue

        was_just_closed_expr_if = just_closed_expr_if
        just_closed_expr_if = False

        cur_scope = scope_stack[-1]
        is_stmt_lvl = (
            cur_scope['paren'] == 0
            and cur_scope['bracket'] == 0
            and cur_scope['brace'] == 0
            and if_expr_depth == 0
        )

        if (
            is_stmt_lvl
            and cur_line
            and tk.kind == 'word'
            and val not in LUA_KEYWORDS
            and prev_tk is not None
            and (
                prev_tk.kind in ('number', 'string')
                or prev_val in (')', ']', '}', 'true', 'false', 'nil', '...')
                or (prev_tk.kind == 'word' and prev_val not in LUA_KEYWORDS)
            )
        ):
            flush()

        if val in STMT_STARTERS and is_stmt_lvl:
            if val == 'repeat':
                flush()
                cur_line.append(tk)
                flush()
                indent_level += 1
                scope_stack.append({'type': 'repeat', 'paren': 0, 'bracket': 0, 'brace': 0})
                i += 1
                continue
            flush()
            cur_line.append(tk)
            i += 1
            continue

        if val == 'if':
            is_luau_expr_if = (
                is_expr_if(tokens, i)
                or (if_expr_depth > 0 and prev_val in ('then', 'elseif'))
                or (was_just_closed_expr_if and prev_val == 'else')
            )
            if is_luau_expr_if:
                if_expr_depth += 1
                cur_line.append(tk)
            else:
                flush()
                cur_line.append(tk)
                scope_stack.append({'type': 'if_cond', 'paren': 0, 'bracket': 0, 'brace': 0})
            i += 1
            continue

        if val == 'then':
            cur_line.append(tk)
            if if_expr_depth == 0:
                if len(scope_stack) > 1 and scope_stack[-1]['type'] == 'if_cond':
                    scope_stack[-1]['type'] = 'block'
                flush()
                indent_level += 1
            i += 1
            continue

        if val in ('else', 'elseif'):
            if if_expr_depth > 0:
                cur_line.append(tk)
                if val == 'else':
                    if_expr_depth -= 1
                    just_closed_expr_if = True
            elif is_stmt_lvl:
                flush()
                indent_level = max(0, indent_level - 1)
                cur_line.append(tk)
                if val == 'else':
                    flush()
                    indent_level += 1
            else:
                cur_line.append(tk)
            i += 1
            continue

        if val == 'do':
            if is_stmt_lvl:
                has_loop_hdr = any(t.val in ('while', 'for') for t in cur_line)
                if not has_loop_hdr:
                    flush()
                cur_line.append(tk)
                flush()
                indent_level += 1
                scope_stack.append({'type': 'block', 'paren': 0, 'bracket': 0, 'brace': 0})
            else:
                cur_line.append(tk)
            i += 1
            continue

        if val == 'until':
            if len(scope_stack) > 1 and scope_stack[-1]['type'] == 'repeat':
                scope_stack.pop()
                flush()
                indent_level = max(0, indent_level - 1)
            elif is_stmt_lvl:
                flush()
                indent_level = max(0, indent_level - 1)
            cur_line.append(tk)
            i += 1
            continue

        if val == 'end':
            flush()
            indent_level = max(0, indent_level - 1)
            if len(scope_stack) > 1 and scope_stack[-1]['type'] in ('function', 'block', 'if_cond'):
                scope_stack.pop()
            cur_line.append(tk)
            if next_val not in (')', ',', ';', '.', ':', '('):
                flush()
            i += 1
            continue

        if val == 'function':
            if is_stmt_lvl and prev_val not in ('local', 'return', '=', ':', '.', '(', '{', ',', ';', 'and', 'or'):
                flush()
            cur_line.append(tk)
            i += 1
            p_depth = 0
            seen_open = False
            while i < n:
                cur_line.append(tokens[i])
                if tokens[i].val == '(':
                    p_depth += 1
                    seen_open = True
                elif tokens[i].val == ')':
                    p_depth -= 1
                    if seen_open and p_depth <= 0:
                        i += 1
                        break
                i += 1
            flush()
            indent_level += 1
            scope_stack.append({'type': 'function', 'paren': 0, 'bracket': 0, 'brace': 0})
            continue

        if val == '(':
            cur_scope['paren'] += 1
            cur_line.append(tk)
            i += 1
            continue

        if val == ')':
            cur_scope['paren'] = max(0, cur_scope['paren'] - 1)
            cur_line.append(tk)
            i += 1
            continue

        if val == '[':
            cur_scope['bracket'] += 1
            cur_line.append(tk)
            i += 1
            continue

        if val == ']':
            cur_scope['bracket'] = max(0, cur_scope['bracket'] - 1)
            cur_line.append(tk)
            i += 1
            continue

        if val == '{':
            if next_val == '}':
                cur_line.append(Token('symbol', '{}', tk.start, next_tk.end if next_tk else tk.end))
                i += 2
                continue
            cur_scope['brace'] += 1
            cur_line.append(tk)
            flush()
            indent_level += 1
            i += 1
            continue

        if val == '}':
            cur_scope['brace'] = max(0, cur_scope['brace'] - 1)
            flush()
            indent_level = max(0, indent_level - 1)
            cur_line.append(tk)
            if next_val not in (',', ';', ')', ']', '.'):
                flush()
            i += 1
            continue

        if val == ',':
            cur_line.append(tk)
            if cur_scope['brace'] > 0 and cur_scope['paren'] == 0 and cur_scope['bracket'] == 0:
                flush()
            i += 1
            continue

        if val == ';':
            if cur_scope['brace'] > 0 and cur_scope['paren'] == 0:
                cur_line.append(tk)
            flush()
            i += 1
            continue

        cur_line.append(tk)
        i += 1

    flush()
    return '\n'.join(out_lines)


def fold_lua_code(code: str, unescape_strings: bool = False, simulate_seeded_rng: bool = False, pretty: bool = False) -> str:
    """Tokenize, constant-fold, and serialize (compact or pretty-printed) Lua source."""
    toks = tokenize_lua(code)
    folded = fold_tokens(toks, unescape_strings=unescape_strings, simulate_seeded_rng=simulate_seeded_rng)
    return pretty_print_lua(folded) if pretty else tokens_to_compact_lua(folded)



def try_unwrap_loadstring_packer(code: str) -> tuple[str | None, str | None]:
    """Detect and unwrap outer loadstring/load or byte-array packer wrappers."""
    folded_toks = fold_tokens(tokenize_lua(code), unescape_strings=False)
    non_comment = [t for t in folded_toks if t.kind != 'comment']
    if not non_comment:
        return None, None

    compact = tokens_to_compact_lua(non_comment)
    if len(non_comment) <= 25:
        for idx, t in enumerate(non_comment):
            if t.val in ('loadstring', 'load') and idx + 3 < len(non_comment):
                if non_comment[idx + 1].val == '(' and non_comment[idx + 2].kind == 'string' and non_comment[idx + 3].val == ')':
                    payload_bytes = parse_lua_string_literal(non_comment[idx + 2].val)
                    try:
                        inner = payload_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        inner = payload_bytes.decode('latin1')
                    if inner.strip():
                        return inner, f"{t.val}() string payload ({len(payload_bytes)} bytes)"

    if ('loadstring' in compact or 'load(' in compact) and 'string.char' in compact:
        m_arr = re.search(r'\{\s*(\d+(?:\s*[,;]\s*\d+){10,})\s*[,;]?\s*\}', compact)
        if m_arr:
            raw_nums = [int(x.strip()) for x in re.split(r'[,;]', m_arr.group(1)) if x.strip()]
            if all(0 <= b <= 255 for b in raw_nums):
                after_arr = compact[m_arr.end() :]
                dec_bytes: list[int] | None = None
                desc = ''

                m_xor = re.search(r'\b(?:bxor|bit32\.bxor|bit\.bxor)\s*\([^,]+,\s*(\d+)\s*\)', after_arr)
                m_sub = re.search(r'\(\s*\w+\[\w+\]\s*-\s*(\d+)\s*\)\s*%\s*256', after_arr)
                m_add = re.search(r'\(\s*\w+\[\w+\]\s*\+\s*(\d+)\s*\)\s*%\s*256', after_arr)
                if m_xor:
                    k = int(m_xor.group(1))
                    dec_bytes = [(b ^ k) & 0xFF for b in raw_nums]
                    desc = f"byte-array XOR (key={k}) + loadstring ({len(raw_nums)} bytes)"
                elif m_sub:
                    k = int(m_sub.group(1))
                    dec_bytes = [(b - k) % 256 for b in raw_nums]
                    desc = f"byte-array modular-sub (key={k}) + loadstring ({len(raw_nums)} bytes)"
                elif m_add:
                    k = int(m_add.group(1))
                    dec_bytes = [(b + k) % 256 for b in raw_nums]
                    desc = f"byte-array modular-add (key={k}) + loadstring ({len(raw_nums)} bytes)"
                elif re.search(r'string\.char\s*\(\s*\w+\[\w+\]\s*\)', after_arr):
                    dec_bytes = raw_nums
                    desc = f"byte-array string.char + loadstring ({len(raw_nums)} bytes)"

                if dec_bytes is not None:
                    raw = bytes(dec_bytes)
                    try:
                        inner = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        inner = raw.decode('latin1')
                    if inner.strip():
                        return inner, desc

    return None, None



@dataclasses.dataclass
class PrometheusLayer1:
    tbl_var: str
    idx_fn: str | None
    offset: int
    strings_bytes: list[bytes]       # 1-indexed (index 0 is b'')
    strings_latin1: list[str]        # 1-indexed (index 0 is '')
    header_end_pos: int              # End of Layer-1 `do ... end` decoder block in original code
    has_vm: bool


def _parse_alphabet_table(table_inner: str) -> dict[str, int]:
    """Fold math inside a Prometheus alphabet lookup table and extract char -> int map."""
    folded = fold_lua_code('{' + table_inner + '}')
    mapping: dict[str, int] = {}
    for m in re.finditer(r'(?:\["((?:\\.|[^"\\])*)"\]|\[\'((?:\\.|[^\'\\])*)\'\]|([A-Za-z_]\w*))\s*=\s*(-?\d+)', folded):
        if m.group(1) is not None:
            key_bytes = parse_lua_string_literal('"' + m.group(1) + '"')
            key = key_bytes.decode('latin1')
        elif m.group(2) is not None:
            key_bytes = parse_lua_string_literal("'" + m.group(2) + "'")
            key = key_bytes.decode('latin1')
        else:
            key = m.group(3)
        if len(key) == 1:
            mapping[key] = int(m.group(4))
    return mapping


def detect_prometheus(code: str) -> PrometheusLayer1 | None:
    """Detect Prometheus obfuscation (string table and optional VM)."""
    toks = tokenize_lua(code)
    tbl_var = None
    brace_open_idx = -1
    for i in range(min(30, len(toks) - 4)):
        if (
            toks[i].val == 'local'
            and toks[i + 1].kind == 'word'
            and toks[i + 2].val == '='
            and toks[i + 3].val == '{'
            and toks[i + 4].kind == 'string'
        ):
            tbl_var = toks[i + 1].val
            brace_open_idx = i + 3
            break

    if tbl_var is None:
        return None

    raw_items: list[bytes] = []
    j = brace_open_idx + 1
    valid_array = True
    while j < len(toks) and toks[j].val != '}':
        if toks[j].kind == 'string':
            raw_items.append(parse_lua_string_literal(toks[j].val))
        elif toks[j].val in (',', ';') or toks[j].kind == 'comment':
            pass
        else:
            valid_array = False
            break
        j += 1

    if not valid_array or not raw_items or j >= len(toks):
        return None

    after_table_pos = toks[j].end
    rest_head = code[after_table_pos : after_table_pos + 16000]

    tables: list[dict[str, int]] = []
    for tm in re.finditer(r'local\s+[A-Za-z_]\w*\s*=\s*\{([^{}]{250,4500})\}', rest_head):
        mp = _parse_alphabet_table(tm.group(1))
        if len(mp) in (64, 85):
            tables.append(mp)

    ascii85_map = next((t for t in tables if len(t) == 85), None)
    base64_map = next((t for t in tables if len(t) == 64), None)
    if not ascii85_map and not base64_map:
        return None

    pairs: list[tuple[int, int]] = []
    m_ipairs = re.search(r'ipairs\s*\(\s*\{(\{.*?\})\}\s*\)', rest_head, flags=re.DOTALL)
    if m_ipairs:
        folded_pairs = fold_lua_code('{' + m_ipairs.group(1) + '}')
        for pm in re.finditer(r'\{\s*(-?\d+)\s*[,;]\s*(-?\d+)\s*\}', folded_pairs):
            pairs.append((int(pm.group(1)), int(pm.group(2))))

    arr: list[bytes] = [b''] + list(raw_items)  # 1-indexed
    for a, b in pairs:
        while 1 <= a < b < len(arr):
            arr[a], arr[b] = arr[b], arr[a]
            a += 1
            b -= 1

    idx_fn = None
    offset = 0
    m_off = re.search(
        rf'local\s+function\s+([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)\s*return\s+{re.escape(tbl_var)}\s*\[\s*\2\s*([+-])\s*\(([^)]+)\)\s*\]\s*end',
        rest_head,
    )
    if not m_off:
        m_off = re.search(
            rf'local\s+function\s+([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)\s*return\s+{re.escape(tbl_var)}\s*\[\s*\2\s*([+-])\s*([^\]]+)\]\s*end',
            rest_head,
        )
    if m_off:
        idx_fn = m_off.group(1)
        sign_ch = m_off.group(3)
        folded_off = fold_lua_code(m_off.group(4)).strip()
        off_num = parse_lua_num(folded_off)
        if off_num is not None:
            offset = int(off_num) if sign_ch == '+' else -int(off_num)

    b64_tag = ord('b')
    a85_tag = ord('L')
    tag_matches = list(re.finditer(r'\b(?:if|elseif)\s+[A-Za-z_]\w*\s*==\s*(["\'])(?:\\.|[^\\])*?\1\s+then\b', rest_head))
    if tag_matches:
        for idx_m, tm in enumerate(tag_matches):
            lit_m = re.search(r'(["\'])(?:\\.|[^\\])*?\1', tm.group(0))
            if not lit_m:
                continue
            tag_b = parse_lua_string_literal(lit_m.group(0))
            if len(tag_b) != 1:
                continue
            next_pos = tag_matches[idx_m + 1].start() if idx_m + 1 < len(tag_matches) else len(rest_head)
            branch_body = rest_head[tm.end() : next_pos]
            if '==' in branch_body and '"="' in branch_body or "'='" in branch_body:
                b64_tag = tag_b[0]
            else:
                a85_tag = tag_b[0]

    for idx in range(1, len(arr)):
        s = arr[idx]
        if not s:
            continue
        prefix = s[0]
        body = s[1:].decode('latin1')
        if prefix == a85_tag and ascii85_map:
            length = len(body)
            out_b = bytearray()
            c = 0
            while c < length:
                rem = length - c
                n_chunk = 5 if rem >= 5 else rem
                b_val = 0
                valid = n_chunk > 1
                for d_i in range(5):
                    if d_i < n_chunk:
                        xv = ascii85_map.get(body[c + d_i])
                        if xv is None:
                            valid = False
                            break
                    else:
                        xv = 84
                    b_val = b_val * 85 + xv
                if valid:
                    out_b.extend([
                        (b_val // 16777216) % 256,
                        (b_val // 65536) % 256,
                        (b_val // 256) % 256,
                        b_val % 256,
                    ][: n_chunk - 1])
                c += n_chunk
            arr[idx] = bytes(out_b)
        elif prefix == b64_tag and base64_map:
            out_b = bytearray()
            acc = 0
            cnt = 0
            for pos, ch in enumerate(body):
                if ch in base64_map:
                    acc += base64_map[ch] * (64 ** (3 - cnt))
                    cnt += 1
                    if cnt == 4:
                        cnt = 0
                        out_b.extend([(acc // 65536) % 256, (acc // 256) % 256, acc % 256])
                        acc = 0
                elif ch == '=':
                    out_b.append((acc // 65536) % 256)
                    if pos + 1 >= len(body) or body[pos + 1] != '=':
                        out_b.append((acc // 256) % 256)
                    break
            arr[idx] = bytes(out_b)

    header_end_pos = after_table_pos
    k = j + 1
    while k < len(toks):
        if toks[k].val == 'do':
            end_k = find_matching_block_end(toks, k)
            if end_k != -1:
                header_end_pos = toks[end_k].end
                break
        elif toks[k].val == 'for':
            end_k = find_matching_block_end(toks, k)
            if end_k != -1:
                k = end_k + 1
                continue
        elif toks[k].val == 'local' and k + 1 < len(toks) and toks[k + 1].val == 'function':
            end_k = find_matching_block_end(toks, k + 1)
            if end_k != -1:
                header_end_pos = toks[end_k].end
                k = end_k + 1
                continue
        k += 1

    after_header = code[header_end_pos:]
    has_vm = bool(
        re.search(
            r'\bwhile\s+([A-Za-z_]\w*)\s+do\s+if\s+(?:\1\s*[<>]|[0-9-][^t]*?[<>]\s*\1\b)',
            after_header,
        )
    )

    strings_latin1 = [''] + [b.decode('latin1') for b in arr[1:]]
    return PrometheusLayer1(
        tbl_var=tbl_var,
        idx_fn=idx_fn,
        offset=offset,
        strings_bytes=arr,
        strings_latin1=strings_latin1,
        header_end_pos=header_end_pos,
        has_vm=has_vm,
    )


def _detect_prometheus_prng_params(leaves: list[tuple[int, int, str]]) -> dict[str, int]:
    """Dynamically extract all 6 Prometheus Layer-2 PRNG constants from folded VM leaf blocks."""
    params = {
        'mul1': 213,
        'add1': 27552854471197,
        'mod1': 35184372088832,  # 2^45
        'mul2': 201,
        'mod2': 257,
        'init_t': 204,
    }
    for _, _, body in leaves:
        if '35184372088832' in body:
            m_lcg = re.search(
                r'=\s*(\d+)\s+[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\[\s*[A-Za-z_]\w*\[\s*\d+\s*\]\s*\]\s*'
                r'[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\s*\*\s*[A-Za-z_]\w*\s+'
                r'[A-Za-z_]\w*\s*=\s*(\d+)\s+[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\s*\+\s*[A-Za-z_]\w*\s+'
                r'[A-Za-z_]\w*\s*=\s*(35184372088832)',
                body,
            )
            if m_lcg:
                params['mul1'] = int(m_lcg.group(1))
                params['add1'] = int(m_lcg.group(2))
                params['mod1'] = int(m_lcg.group(3))
            if '255' in body:
                m_init = re.search(r'=\s*[A-Za-z_]\w*\s*,\s*(\d+)\s*,\s*0\b', body)
                if m_init:
                    params['init_t'] = int(m_init.group(1))
        if '257' in body:
            m_mul2 = re.search(
                r'=\s*(\d+)\s+[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\[\s*[A-Za-z_]\w*\[\s*\d+\s*\]\s*\]\s*'
                r'[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\s*\*\s*[A-Za-z_]\w*\s+'
                r'[A-Za-z_]\w*\s*=\s*(257)\b',
                body,
            )
            if m_mul2:
                params['mul2'] = int(m_mul2.group(1))
                params['mod2'] = int(m_mul2.group(2))
    return params


def decrypt_prometheus_layer2(enc_str: str, seed: int, prng_params: dict[str, int] | None = None) -> str:
    """Decrypt a Prometheus Layer-2 string using its numeric seed."""
    p = prng_params or {
        'mul1': 213, 'add1': 27552854471197, 'mod1': 35184372088832,
        'mul2': 201, 'mod2': 257, 'init_t': 204,
    }
    buf: list[int] = []
    state1 = seed % p['mod1']
    state2 = (seed % 255) + 2

    def next_byte() -> int:
        nonlocal buf, state1, state2
        if not buf:
            state1 = (state1 * p['mul1'] + p['add1']) % p['mod1']
            while True:
                state2 = (state2 * p['mul2']) % p['mod2']
                if state2 != 1:
                    break
            rot = state2 % 32
            shift_idx = (state2 - rot) // 32
            exp = 13 - shift_idx
            low32 = int(state1 / (2 ** exp)) % 4294967296
            shifted = low32 / (2 ** rot)
            frac = shifted % 1
            rotated32 = int(frac * 4294967296) + int(shifted)
            lo16 = rotated32 % 65536
            hi16 = (rotated32 - lo16) // 65536
            b0 = lo16 % 256
            b1 = (lo16 - b0) // 256
            b2 = hi16 % 256
            b3 = (hi16 - b2) // 256
            buf = [b0, b1, b2, b3]
        return buf.pop()

    t = p['init_t']
    out_bytes = bytearray()
    for ch in enc_str:
        t = (ord(ch) + next_byte() + t) % 256
        out_bytes.append(t)
    try:
        return out_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return out_bytes.decode('latin1')


def _extract_prometheus_vm_states(folded_vm: str) -> tuple[str | None, list[tuple[int, int, str]]]:
    """Parse Prometheus binary-search dispatcher into state transitions."""
    toks = tokenize_lua(folded_vm)
    state_var = None
    while_idx = -1
    for i in range(len(toks) - 6):
        if toks[i].val == 'while' and toks[i + 1].kind == 'word' and toks[i + 2].val == 'do' and toks[i + 3].val == 'if':
            sv = toks[i + 1].val
            cond_slice = [t.val for t in toks[i + 4 : min(len(toks), i + 10)]]
            if sv in cond_slice and ('<' in cond_slice or '>' in cond_slice):
                state_var = sv
                while_idx = i
                break

    if state_var is None or while_idx == -1:
        return None, []

    do_idx = while_idx + 2
    end_idx = find_matching_block_end(toks, do_idx)
    if end_idx == -1:
        return state_var, []

    tree_code = folded_vm[toks[do_idx].end : toks[end_idx].start]
    sv_esc = re.escape(state_var)
    token_re = re.compile(
        rf'\b(?:if|elseif)\s+({sv_esc}\s*[<>]\s*-?\d+|-?\d+\s*[<>]\s*{sv_esc})\s+then\b|\belse\b|\bend\b'
    )
    branch_toks = list(token_re.finditer(tree_code))

    def parse_cond(cond_str: str) -> tuple[str, int]:
        c = re.sub(r'\s+', '', cond_str)
        if c.startswith(state_var + '<'):
            return ('<', int(c[len(state_var) + 1 :]))
        if c.startswith(state_var + '>'):
            return ('>', int(c[len(state_var) + 1 :]))
        if c.endswith('>' + state_var):
            return ('<', int(c[: -(len(state_var) + 1)]))
        if c.endswith('<' + state_var):
            return ('>', int(c[: -(len(state_var) + 1)]))
        raise ValueError(f"Unrecognized VM state condition: {cond_str}")

    leaves: list[tuple[int, int, str]] = []

    def parse_block(idx: int, low: int, high: int, start_char: int) -> int:
        if (
            idx < len(branch_toks)
            and tree_code[start_char : branch_toks[idx].start()].strip() == ''
            and branch_toks[idx].group(0).startswith('if ')
        ):
            tok = branch_toks[idx]
            cond = parse_cond(tok.group(1))
            if cond[0] == '<':
                t_low, t_high = low, min(high, cond[1] - 1)
                f_low, f_high = max(low, cond[1]), high
            else:
                t_low, t_high = max(low, cond[1] + 1), high
                f_low, f_high = low, min(high, cond[1])

            next_idx = parse_block(idx + 1, t_low, t_high, tok.end())
            cur_low, cur_high = f_low, f_high
            while next_idx < len(branch_toks):
                ntok = branch_toks[next_idx]
                ntxt = ntok.group(0)
                if ntxt.startswith('elseif '):
                    econd = parse_cond(ntok.group(1))
                    if econd[0] == '<':
                        et_low, et_high = cur_low, min(cur_high, econd[1] - 1)
                        ef_low, ef_high = max(cur_low, econd[1]), cur_high
                    else:
                        et_low, et_high = max(cur_low, econd[1] + 1), cur_high
                        ef_low, ef_high = cur_low, min(cur_high, econd[1])
                    next_idx = parse_block(next_idx + 1, et_low, et_high, ntok.end())
                    cur_low, cur_high = ef_low, ef_high
                elif ntxt == 'else':
                    next_idx = parse_block(next_idx + 1, cur_low, cur_high, ntok.end())
                elif ntxt == 'end':
                    return next_idx + 1
            return next_idx
        else:
            end_char = branch_toks[idx].start() if idx < len(branch_toks) else len(tree_code)
            leaves.append((low, high, tree_code[start_char:end_char].strip()))
            return idx

    parse_block(0, 0, 10**9, 0)
    return state_var, leaves


def _symbolically_resolve_vm_state(
    body: str,
    D_latin1: list[str],
    tbl_aliases: set[str],
    prng_params: dict[str, int],
) -> tuple[str, list[tuple[int, int, str]]]:
    """Symbolically track `<tbl>[idx]` and integer seeds in a VM state and decrypt Layer-2 calls."""
    clean = re.sub(r'--\[\[.*?\]\]', '', body)
    tokens = re.findall(
        r'[A-Za-z_]\w*(?:\[\s*[A-Za-z_]\w*\[\s*\d+\s*\]\s*\])?(?:\[\s*[A-Za-z0-9_]+\s*\])?|-?\d+(?:\.\d+)?|[,=;{}()]|\.\.|true|false|nil',
        clean,
    )
    env: dict[str, tuple] = {}
    decrypted_pairs: list[tuple[int, int, str]] = []
    pos = 0

    alias_pat = '|'.join(re.escape(a) for a in sorted(tbl_aliases))
    d_lookup_re = re.compile(rf'(?:{alias_pat})\[(\d+)\]')

    while pos < len(tokens):
        lhs = []
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == '=':
                pos += 1
                break
            elif tok == ',':
                pos += 1
            else:
                lhs.append(tok)
                pos += 1
        if not lhs:
            break

        rhs = []
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == '{':
                depth = 1
                expr = [tok]
                pos += 1
                while pos < len(tokens) and depth > 0:
                    if tokens[pos] == '{':
                        depth += 1
                    elif tokens[pos] == '}':
                        depth -= 1
                    expr.append(tokens[pos])
                    pos += 1
                rhs.append(''.join(expr))
            elif pos + 1 < len(tokens) and tokens[pos + 1] == '(':
                fn = tok
                depth = 1
                expr = [fn, '(']
                pos += 2
                while pos < len(tokens) and depth > 0:
                    if tokens[pos] == '(':
                        depth += 1
                    elif tokens[pos] == ')':
                        depth -= 1
                    expr.append(tokens[pos])
                    pos += 1
                rhs.append(''.join(expr))
            else:
                rhs.append(tok)
                pos += 1

            if pos < len(tokens) and tokens[pos] == ',':
                pos += 1
            else:
                break

        vals = []
        for r in rhs:
            m_d = d_lookup_re.fullmatch(r)
            if m_d:
                idx = int(m_d.group(1))
                s_val = D_latin1[idx] if 1 <= idx < len(D_latin1) else ''
                vals.append(('D', idx, s_val))
            elif re.fullmatch(r'-?\d+', r):
                vals.append(('INT', int(r)))
            elif r in env:
                vals.append(env[r])
            else:
                m_c = re.fullmatch(r'([A-Za-z_]\w*)\(([A-Za-z_]\w*),([A-Za-z_]\w*)\)', r)
                if m_c:
                    _, a1, a2 = m_c.group(1), m_c.group(2), m_c.group(3)
                    v1, v2 = env.get(a1), env.get(a2)
                    if (
                        isinstance(v1, tuple) and v1[0] == 'D'
                        and isinstance(v2, tuple) and v2[0] == 'INT'
                    ):
                        dec = decrypt_prometheus_layer2(v1[2], v2[1], prng_params)
                        vals.append(('DEC', dec, v1[1], v2[1]))
                        decrypted_pairs.append((v1[1], v2[1], dec))
                        body = re.sub(
                            rf'\b{re.escape(r)}',
                            lambda _, r=r, dec=dec: f'{r}--[[DECRYPTED: {repr(dec)}]]',
                            body,
                            count=1,
                        )
                    else:
                        vals.append(('EXPR', r))
                else:
                    vals.append(('EXPR', r))

        for l_var, val in zip(lhs, vals):
            env[l_var] = val

    return body, decrypted_pairs


def decode_prometheus_vm(code: str, l1: PrometheusLayer1, pretty: bool = True) -> str:
    """Decode a full Prometheus Layer-1 + Layer-2 PRNG VM script."""
    vm_tail = code[l1.header_end_pos :]
    folded_vm = fold_lua_code(vm_tail, pretty=False)

    state_var, raw_leaves = _extract_prometheus_vm_states(folded_vm)
    prng_params = _detect_prometheus_prng_params(raw_leaves)
    tbl_aliases = {l1.tbl_var}

    def annotate_leaf_idx_calls(leaf_body: str) -> str:
        if not l1.idx_fn:
            return leaf_body

        def repl_idx_fn(m: re.Match) -> str:
            val = int(m.group(1))
            idx = val + l1.offset
            s = l1.strings_latin1[idx] if 1 <= idx < len(l1.strings_latin1) else '???'
            preview = repr(s) if (s.isprintable() and len(s) <= 60) else f'<{len(s)}B enc>'
            return f'{l1.tbl_var}[{idx}]--[[{preview}]]'

        return re.sub(rf'\b{re.escape(l1.idx_fn)}\s*\(\s*(-?\d+)\s*\)', repl_idx_fn, leaf_body)

    leaves = [(lo, hi, annotate_leaf_idx_calls(body)) for (lo, hi, body) in raw_leaves]

    all_decrypted: list[tuple[int, int, str]] = []
    annotated_leaves: list[tuple[int, int, int, str]] = []
    for idx, (low, high, body) in enumerate(leaves):
        ann_body, dec_list = _symbolically_resolve_vm_state(body, l1.strings_latin1, tbl_aliases, prng_params)
        all_decrypted.extend(dec_list)
        if pretty:
            ann_body = pretty_print_lua(tokenize_lua(ann_body))
        annotated_leaves.append((idx, low, high, ann_body))

    lines: list[str] = []
    lines.append('-- Obfuscator: Prometheus (Layer-1 ASCII85/Base64 + Layer-2 PRNG VM)')
    lines.append(f'-- String Table Var    : {l1.tbl_var} ({len(l1.strings_bytes) - 1} entries)')
    lines.append(f'-- Offset Function     : {l1.idx_fn or "<direct>"}(n) -> {l1.tbl_var}[n + ({l1.offset})]')
    lines.append(f'-- VM State Variable   : {state_var} ({len(leaves)} leaf states)')
    lines.append(f'-- Layer-2 PRNG Params : {prng_params}')
    lines.append(f'-- Layer-2 Decrypted   : {len(all_decrypted)} strings')
    lines.append('')
    lines.append('-- [1] LAYER-2 DECRYPTED STRINGS (45-bit LCG + math.floor Rotation + math.random):')
    seen: set[tuple[int, int]] = set()
    for d_idx, seed, dec in all_decrypted:
        if (d_idx, seed) not in seen:
            seen.add((d_idx, seed))
            lines.append(f'--   decrypt({l1.tbl_var}[{d_idx:3d}], {seed:15d}) -> {repr(dec)}')
    lines.append('')
    lines.append(f'-- [2] LAYER-1 READABLE STRINGS ({l1.tbl_var}):')
    for idx in range(1, len(l1.strings_bytes)):
        b = l1.strings_bytes[idx]
        try:
            txt = b.decode('utf-8')
            if txt and txt.isprintable():
                lines.append(f'--   {l1.tbl_var}[{idx:3d}] = {repr(txt)}')
        except UnicodeDecodeError:
            pass
    lines.append('')
    lines.append('-- [3] ANNOTATED & CONSTANT-FOLDED VM STATES:')
    for idx, low, high, ann_body in annotated_leaves:
        lines.append(f'\n-- --- Leaf #{idx} [{state_var} in {low}..{high}] ---')
        lines.append(ann_body)

    return '\n'.join(lines)


def decode_prometheus_strings(code: str, l1: PrometheusLayer1, pretty: bool = True) -> str:
    """Decode Prometheus scripts without Layer-2 VM."""
    body = code[l1.header_end_pos :].strip()
    if re.match(r'^\s*return\s*\(\s*function\s*\(\s*\.\.\.\s*\)', code):
        m_tail = re.search(r'\bend\s*\)\s*\(\s*\.\.\.\s*\)\s*$', body)
        if m_tail:
            body = body[: m_tail.start()].strip()

    toks = fold_tokens(tokenize_lua(body), unescape_strings=True)

    inlined: list[Token] = []
    i = 0
    while i < len(toks):
        if (
            l1.idx_fn
            and i + 3 < len(toks)
            and toks[i].kind == 'word'
            and toks[i].val == l1.idx_fn
            and toks[i + 1].val == '('
            and toks[i + 2].kind == 'number'
            and toks[i + 3].val == ')'
        ):
            num = parse_lua_num(toks[i + 2].val)
            if isinstance(num, int):
                idx = num + l1.offset
                if 1 <= idx < len(l1.strings_bytes):
                    inlined.append(Token('string', bytes_to_lua_string(l1.strings_bytes[idx])))
                    i += 4
                    continue
        if (
            i + 3 < len(toks)
            and toks[i].kind == 'word'
            and toks[i].val == l1.tbl_var
            and toks[i + 1].val == '['
            and toks[i + 2].kind == 'number'
            and toks[i + 3].val == ']'
        ):
            num = parse_lua_num(toks[i + 2].val)
            if isinstance(num, int) and 1 <= num < len(l1.strings_bytes):
                inlined.append(Token('string', bytes_to_lua_string(l1.strings_bytes[num])))
                i += 4
                continue
        inlined.append(toks[i])
        i += 1

    inlined = fold_tokens(inlined)
    formatted = pretty_print_lua(inlined) if pretty else tokens_to_compact_lua(inlined)
    hdr = (
        f'-- Obfuscator: Prometheus String-Table\n'
        f'-- Decoded {len(l1.strings_bytes) - 1} Layer-1 strings ({l1.tbl_var}, offset={l1.offset})\n\n'
    )
    return hdr + formatted



@dataclasses.dataclass
class RollingXorInfo:
    tbl_var: str
    key_k: int
    coef_i: int
    coef_j: int
    strings: list[bytes]       # 1-indexed (index 0 is b'')
    iife_start: int
    iife_end: int
    prefix_comments: str


def _score_printable_utf8(samples: list[bytes]) -> float:
    total = 0
    good = 0
    for b in samples:
        if not b:
            continue
        try:
            s = b.decode('utf-8')
            for ch in s:
                total += 1
                cp = ord(ch)
                if 32 <= cp <= 126 or ch in '\n\r\t':
                    good += 2
                elif cp > 127 and ch.isprintable():
                    good += 1
        except UnicodeDecodeError:
            total += len(b) * 2
    return (good / max(1, total))


def detect_rolling_xor(code: str) -> RollingXorInfo | None:
    """Detect IIFE Rolling-XOR string-table obfuscation."""
    m_hdr = re.search(
        r'local\s+([A-Za-z_]\w*)\s*=\s*\(\s*function\s*\(\s*\)',
        code[:1024],
    )
    if not m_hdr:
        return None

    tbl_var = m_hdr.group(1)
    iife_start = m_hdr.start()
    prefix_comments = code[:iife_start].strip()

    toks = tokenize_lua(code)
    fn_idx = -1
    for idx, t in enumerate(toks[:40]):
        if t.val == 'function' and t.start >= m_hdr.start():
            fn_idx = idx
            break
    if fn_idx == -1:
        return None

    end_idx = find_matching_block_end(toks, fn_idx)
    if end_idx == -1 or end_idx + 3 >= len(toks):
        return None
    if not (toks[end_idx + 1].val == ')' and toks[end_idx + 2].val == '(' and toks[end_idx + 3].val == ')'):
        return None

    iife_end = toks[end_idx + 3].end
    iife_toks = toks[fn_idx : end_idx + 1]
    iife_text_short = (
        code[m_hdr.start() : min(len(code), m_hdr.start() + 600)]
        + '\n'
        + code[max(m_hdr.start(), toks[end_idx].start - 600) : toks[end_idx].end]
    )
    if 'bxor' not in iife_text_short and '%' not in iife_text_short:
        return None

    arr_open = -1
    for k in range(len(iife_toks) - 3):
        if iife_toks[k].val == '{' and iife_toks[k + 1].kind == 'string':
            arr_open = k
            break
    if arr_open == -1:
        return None

    raw_entries: list[bytes] = []
    k = arr_open + 1
    while k < len(iife_toks) and iife_toks[k].val != '}':
        if iife_toks[k].kind == 'string':
            raw_entries.append(parse_lua_string_literal(iife_toks[k].val))
        k += 1
    if not raw_entries:
        return None

    arr_close = k
    stub_toks = iife_toks[:arr_open] + [Token('symbol', '{}')] + iife_toks[arr_close + 1 :]
    folded_stub = tokens_to_compact_lua(fold_tokens(stub_toks))

    for_vars = re.findall(r'\bfor\s+([A-Za-z_]\w*)\s*=\s*1\b', folded_stub)
    ivar = for_vars[0] if len(for_vars) >= 1 else 'i'
    jvar = for_vars[1] if len(for_vars) >= 2 else 'j'

    coef_i = 1 if re.search(rf'\+\s*{re.escape(ivar)}\b|\b{re.escape(ivar)}\s*\+', folded_stub) else 0
    coef_j = 1 if re.search(rf'\+\s*{re.escape(jvar)}\b|\b{re.escape(jvar)}\s*\+', folded_stub) else 0

    key_k: int | None = None
    for m_k in re.finditer(r'\blocal\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\b', folded_stub):
        cand = int(m_k.group(2))
        if 0 <= cand <= 65535:
            key_k = cand % 256
            break

    sample_entries = [(idx, b) for idx, b in enumerate(raw_entries[:30], 1) if b][:20]
    if key_k is None and sample_entries:
        best_k, best_score = 0, -1.0
        for cand_k in range(256):
            test_dec = [
                bytes(b ^ ((cand_k + coef_i * i_idx + coef_j * j_idx) % 256) for j_idx, b in enumerate(eb, 1))
                for i_idx, eb in sample_entries
            ]
            sc = _score_printable_utf8(test_dec)
            if sc > best_score:
                best_score = sc
                best_k = cand_k
        key_k = best_k

    if key_k is None:
        return None

    decoded_entries: list[bytes] = [b'']
    for i_idx, eb in enumerate(raw_entries, 1):
        dec = bytes(b ^ ((key_k + coef_i * i_idx + coef_j * j_idx) % 256) for j_idx, b in enumerate(eb, 1))
        decoded_entries.append(dec)

    return RollingXorInfo(
        tbl_var=tbl_var,
        key_k=key_k,
        coef_i=coef_i,
        coef_j=coef_j,
        strings=decoded_entries,
        iife_start=iife_start,
        iife_end=iife_end,
        prefix_comments=prefix_comments,
    )


def _strip_leading_honeypot_do_block(tokens: list[Token]) -> tuple[list[Token], bool]:
    """Strip leading dead-code honeypot do...end block if present."""
    idx = 0
    while idx < len(tokens) and tokens[idx].kind == 'comment':
        idx += 1
    if idx >= len(tokens) or tokens[idx].val != 'do':
        return tokens, False

    end_idx = find_matching_block_end(tokens, idx)
    if end_idx == -1:
        return tokens, False

    k = idx + 1
    only_locals = True
    saw_local = False
    while k < end_idx:
        tk = tokens[k]
        if tk.kind == 'comment' or tk.val == ';':
            k += 1
            continue
        if tk.val != 'local' or k + 2 >= end_idx or tokens[k + 1].kind != 'word' or tokens[k + 2].val != '=':
            only_locals = False
            break
        saw_local = True
        rhs_tok = tokens[k + 3] if k + 3 < end_idx else None
        if rhs_tok is None:
            only_locals = False
            break
        if rhs_tok.val == 'function':
            fn_end = find_matching_block_end(tokens, k + 3)
            if fn_end == -1 or fn_end >= end_idx:
                only_locals = False
                break
            k = fn_end + 1
        elif rhs_tok.val == '{':
            depth = 1
            k = k + 4
            while k < end_idx and depth > 0:
                if tokens[k].val == '{':
                    depth += 1
                elif tokens[k].val == '}':
                    depth -= 1
                k += 1
        elif rhs_tok.kind in ('number', 'string') or rhs_tok.val in ('true', 'false', 'nil'):
            k = k + 4
        else:
            only_locals = False
            break

    if saw_local and only_locals:
        return tokens[:idx] + tokens[end_idx + 1 :], True
    return tokens, False


def decode_rolling_xor(code: str, info: RollingXorInfo, pretty: bool = True) -> str:
    """Decode a Rolling-XOR string-table obfuscated Lua script and pretty-print it."""
    remainder = code[info.iife_end :]
    rem_toks = tokenize_lua(remainder)
    rem_toks, stripped_honeypot = _strip_leading_honeypot_do_block(rem_toks)

    rem_toks = fold_tokens(rem_toks)

    sub_toks: list[Token] = []
    replaced_count = 0
    i = 0
    while i < len(rem_toks):
        if (
            i + 3 < len(rem_toks)
            and rem_toks[i].kind == 'word'
            and rem_toks[i].val == info.tbl_var
            and rem_toks[i + 1].val == '['
            and rem_toks[i + 2].kind == 'number'
            and rem_toks[i + 3].val == ']'
        ):
            idx = parse_lua_num(rem_toks[i + 2].val)
            if isinstance(idx, int) and 1 <= idx < len(info.strings):
                sub_toks.append(Token('string', bytes_to_lua_string(info.strings[idx])))
                replaced_count += 1
                i += 4
                continue
        sub_toks.append(rem_toks[i])
        i += 1

    sub_toks = fold_tokens(sub_toks)
    formatted = pretty_print_lua(sub_toks) if pretty else tokens_to_compact_lua(sub_toks)

    hdr_lines = [
        '-- ============================================================================',
        '-- OBFUSCATOR DETECTED : Rolling-XOR String-Table Obfuscator',
        f'-- String Table        : {info.tbl_var} ({len(info.strings) - 1} strings decoded, {replaced_count} lookups inlined)',
        f'-- Rolling XOR Formula : (K={info.key_k} + {info.coef_i}*i + {info.coef_j}*j) % 256',
        f'-- Dead Honeypot Block : {"Stripped" if stripped_honeypot else "None"}',
        '-- ============================================================================',
    ]
    if info.prefix_comments:
        hdr_lines.append(info.prefix_comments)
    hdr_lines.append('')
    return '\n'.join(hdr_lines) + formatted



@dataclasses.dataclass
class CustomLcgCipher:
    fn_name: str
    mul: int
    add: int
    mod: int
    div: int
    op: str            # 'bxor', 'sub', 'add'
    fn_start: int
    fn_end: int


def detect_seeded_prng_cipher(code: str) -> tuple[str, CustomLcgCipher | None] | None:
    """Detect custom LCG string ciphers or deterministic math.randomseed usage."""
    folded_preview = fold_lua_code(code[: min(len(code), 20000)])

    toks = tokenize_lua(code)
    for idx in range(len(toks) - 6):
        if toks[idx].val == 'local' and toks[idx + 1].val == 'function' and toks[idx + 2].kind == 'word':
            fn_name = toks[idx + 2].val
            fn_end_idx = find_matching_block_end(toks, idx + 1)
            if fn_end_idx == -1:
                continue
            fn_body = fold_lua_code(code[toks[idx].start : toks[fn_end_idx].end])
            m_lcg = re.search(
                r'([A-Za-z_]\w*)\s*=\s*\(\s*\1\s*\*\s*(\d+)\s*\+\s*(\d+)\s*\)\s*%\s*(\d+)',
                fn_body,
            )
            if m_lcg and ('string.char' in fn_body or 'char' in fn_body):
                mul = int(m_lcg.group(2))
                add = int(m_lcg.group(3))
                mod = int(m_lcg.group(4))
                m_div = re.search(r'math\.floor\s*\(\s*' + re.escape(m_lcg.group(1)) + r'\s*/\s*(\d+)\s*\)', fn_body)
                div = int(m_div.group(1)) if m_div else 1
                if 'bxor' in fn_body:
                    op = 'bxor'
                elif re.search(r'-\s*[A-Za-z_]\w*\s*\)\s*%\s*256', fn_body):
                    op = 'sub'
                else:
                    op = 'add'
                return (
                    'custom_lcg',
                    CustomLcgCipher(
                        fn_name=fn_name,
                        mul=mul,
                        add=add,
                        mod=mod,
                        div=div,
                        op=op,
                        fn_start=toks[idx].start,
                        fn_end=toks[fn_end_idx].end,
                    ),
                )

    if re.search(r'\bmath\.randomseed\s*\(\s*-?\d+\s*\)', folded_preview) and 'math.random' in folded_preview:
        return ('luau_randomseed', None)

    return None


def _apply_lcg_decrypt(raw_bytes: list[int], seed: int, cfg: CustomLcgCipher) -> bytes:
    s = seed
    out = bytearray()
    for b in raw_bytes:
        s = (s * cfg.mul + cfg.add) % cfg.mod
        k = (s // cfg.div) % 256
        if cfg.op == 'bxor':
            out.append((b ^ k) & 0xFF)
        elif cfg.op == 'sub':
            out.append((b - k) % 256)
        else:
            out.append((b + k) % 256)
    return bytes(out)


def decode_seeded_prng_cipher(code: str, mode_info: tuple[str, CustomLcgCipher | None], pretty: bool = True) -> str:
    """Decode custom LCG math stream ciphers or Luau `math.randomseed` + `math.random` scripts."""
    submode, cfg = mode_info

    if submode == 'custom_lcg' and cfg is not None:
        stripped = code[: cfg.fn_start] + code[cfg.fn_end :]
        folded = fold_lua_code(stripped, unescape_strings=False, pretty=False)

        dec_count = 0

        def repl_str_call(m: re.Match) -> str:
            nonlocal dec_count
            raw = list(parse_lua_string_literal(m.group(1)))
            seed = int(m.group(2))
            dec_count += 1
            return bytes_to_lua_string(_apply_lcg_decrypt(raw, seed, cfg))

        def repl_tbl_call(m: re.Match) -> str:
            nonlocal dec_count
            nums = [int(x.strip()) for x in re.split(r'[,;]', m.group(1)) if x.strip()]
            seed = int(m.group(2))
            dec_count += 1
            return bytes_to_lua_string(_apply_lcg_decrypt(nums, seed, cfg))

        folded = re.sub(
            rf'\b{re.escape(cfg.fn_name)}\s*\(\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*,\s*(-?\d+)\s*\)',
            repl_str_call,
            folded,
        )
        folded = re.sub(
            r'\b' + re.escape(cfg.fn_name) + r'\s*\(\s*\{\s*(\d+(?:\s*[,;]\s*\d+)*)\s*[,;]?\s*\}\s*,\s*(-?\d+)\s*\)',
            repl_tbl_call,
            folded,
        )
        result = fold_lua_code(folded, unescape_strings=True, pretty=pretty)
        hdr = (
            '-- ============================================================================\n'
            '-- OBFUSCATOR DETECTED : Custom Math LCG PRNG String Cipher\n'
            f'-- Decryptor Function  : {cfg.fn_name}(data, seed) [{dec_count} calls decrypted]\n'
            f'-- LCG Parameters      : seed = (seed * {cfg.mul} + {cfg.add}) % {cfg.mod}, div={cfg.div}, op={cfg.op}\n'
            '-- ============================================================================\n\n'
        )
        return hdr + result

    toks = tokenize_lua(code)
    folded_toks = fold_tokens(toks, unescape_strings=True, simulate_seeded_rng=True)
    cleaned: list[Token] = []
    i = 0
    while i < len(folded_toks):
        if (
            i + 5 < len(folded_toks)
            and folded_toks[i].val == 'math'
            and folded_toks[i + 1].val == '.'
            and folded_toks[i + 2].val == 'randomseed'
            and folded_toks[i + 3].val == '('
            and folded_toks[i + 4].kind == 'number'
            and folded_toks[i + 5].val == ')'
        ):
            i += 6
            if i < len(folded_toks) and folded_toks[i].val == ';':
                i += 1
            continue
        cleaned.append(folded_toks[i])
        i += 1

    cleaned = fold_tokens(cleaned)
    formatted = pretty_print_lua(cleaned) if pretty else tokens_to_compact_lua(cleaned)
    hdr = (
        '-- Obfuscator: Seeded math.randomseed + math.random (Luau PCG32)\n\n'
    )
    return hdr + formatted



@dataclasses.dataclass
class ConstantArrayInfo:
    tbl_var: str
    idx_fn: str | None
    offset: int
    strings: list[bytes]       # 1-indexed (index 0 is b'')
    header_end_pos: int


def detect_constant_array(code: str) -> ConstantArrayInfo | None:
    """Detect constant array string tables with optional shift loops."""
    toks = tokenize_lua(code)
    tbl_var = None
    open_idx = -1
    decl_start_idx = -1
    for i in range(min(25, len(toks) - 5)):
        if (
            toks[i].val == 'local'
            and toks[i + 1].kind == 'word'
            and toks[i + 2].val == '='
            and toks[i + 3].val == '{'
            and toks[i + 4].kind == 'string'
        ):
            tbl_var = toks[i + 1].val
            decl_start_idx = i
            open_idx = i + 3
            break

    if tbl_var is None or decl_start_idx != 0 and any(t.kind != 'comment' for t in toks[:decl_start_idx]):
        return None

    items: list[bytes] = []
    j = open_idx + 1
    while j < len(toks) and toks[j].val != '}':
        if toks[j].kind == 'string':
            items.append(parse_lua_string_literal(toks[j].val))
        elif toks[j].val in (',', ';') or toks[j].kind == 'comment':
            pass
        else:
            return None
        j += 1

    if len(items) < 3 or j >= len(toks):
        return None

    header_end_pos = toks[j].end
    rest_preview = fold_lua_code(code[header_end_pos : header_end_pos + 4000])

    m_rot = re.match(
        rf'^\s*;?\s*for\s+[A-Za-z_]\w*\s*=\s*1\s*,\s*(\d+)\s+do\s+table\.insert\s*\(\s*{re.escape(tbl_var)}\s*,\s*table\.remove\s*\(\s*{re.escape(tbl_var)}\s*,\s*1\s*\)\s*\)\s*end',
        rest_preview,
    )
    if m_rot:
        shift = int(m_rot.group(1)) % len(items)
        items = items[shift:] + items[:shift]
        k = j + 1
        while k < len(toks) and (toks[k].kind == 'comment' or toks[k].val == ';'):
            k += 1
        if k < len(toks) and toks[k].val == 'for':
            end_k = find_matching_block_end(toks, k)
            if end_k != -1:
                header_end_pos = toks[end_k].end
                j = end_k
                rest_preview = fold_lua_code(code[header_end_pos : header_end_pos + 4000])

    idx_fn = None
    offset = 0
    m_fn = re.match(
        rf'^\s*;?\s*local\s+function\s+([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)\s*return\s+{re.escape(tbl_var)}\s*\[\s*\2\s*(?:([+-])\s*(-?\d+))?\s*\]\s*end',
        rest_preview,
    )
    if m_fn:
        idx_fn = m_fn.group(1)
        if m_fn.group(3) and m_fn.group(4):
            off_v = int(m_fn.group(4))
            offset = off_v if m_fn.group(3) == '+' else -off_v
        k = j + 1
        while k < len(toks) and (toks[k].kind == 'comment' or toks[k].val == ';'):
            k += 1
        if k + 1 < len(toks) and toks[k].val == 'local' and toks[k + 1].val == 'function':
            end_k = find_matching_block_end(toks, k + 1)
            if end_k != -1:
                header_end_pos = toks[end_k].end

    body = code[header_end_pos:]
    ref_pat = rf'\b{re.escape(idx_fn)}\s*\(' if idx_fn else rf'\b{re.escape(tbl_var)}\s*\['
    if len(re.findall(ref_pat, body)) < 2:
        return None
    if re.search(rf'\b{re.escape(tbl_var)}\s*\[[^\]]+\]\s*=(?!=)', body):
        return None

    return ConstantArrayInfo(
        tbl_var=tbl_var,
        idx_fn=idx_fn,
        offset=offset,
        strings=[b''] + items,
        header_end_pos=header_end_pos,
    )


def decode_constant_array(code: str, info: ConstantArrayInfo, pretty: bool = True) -> str:
    """Inline all constant-array / WeAreDevs string lookups, fold math, and pretty-print."""
    prefix = code[: code.find('local')].strip()
    body = code[info.header_end_pos :]
    toks = fold_tokens(tokenize_lua(body), unescape_strings=True)

    out_toks: list[Token] = []
    inlined_cnt = 0
    i = 0
    while i < len(toks):
        if (
            info.idx_fn
            and i + 3 < len(toks)
            and toks[i].kind == 'word'
            and toks[i].val == info.idx_fn
            and toks[i + 1].val == '('
            and toks[i + 2].kind == 'number'
            and toks[i + 3].val == ')'
        ):
            num = parse_lua_num(toks[i + 2].val)
            if isinstance(num, int):
                idx = num + info.offset
                if 1 <= idx < len(info.strings):
                    out_toks.append(Token('string', bytes_to_lua_string(info.strings[idx])))
                    inlined_cnt += 1
                    i += 4
                    continue
        if (
            i + 3 < len(toks)
            and toks[i].kind == 'word'
            and toks[i].val == info.tbl_var
            and toks[i + 1].val == '['
            and toks[i + 2].kind == 'number'
            and toks[i + 3].val == ']'
        ):
            idx = parse_lua_num(toks[i + 2].val)
            if isinstance(idx, int) and 1 <= idx < len(info.strings):
                out_toks.append(Token('string', bytes_to_lua_string(info.strings[idx])))
                inlined_cnt += 1
                i += 4
                continue
        out_toks.append(toks[i])
        i += 1

    out_toks = fold_tokens(out_toks)
    formatted = pretty_print_lua(out_toks) if pretty else tokens_to_compact_lua(out_toks)
    hdr = (
        '-- ============================================================================\n'
        '-- OBFUSCATOR DETECTED : Constant-Array / WeAreDevs String-Table Packer\n'
        f'-- String Array        : {info.tbl_var} ({len(info.strings) - 1} entries, {inlined_cnt} lookups inlined)\n'
        '-- ============================================================================\n'
    )
    if prefix:
        hdr += prefix + '\n'
    return hdr + '\n' + formatted



SUPPORTED_ALGOS = (
    'auto',
    'loadstring_packer',
    'prometheus_vm',
    'prometheus_strings',
    'rolling_xor_table',
    'seeded_prng_cipher',
    'constant_array',
    'math_expression',
)


def detect_obfuscator(code: str) -> tuple[str, object]:
    """Fingerprint code and return (algorithm_name, parsed_metadata)."""
    unwrapped, desc = try_unwrap_loadstring_packer(code)
    if unwrapped is not None:
        return ('loadstring_packer', (unwrapped, desc))

    prom = detect_prometheus(code)
    if prom is not None:
        return ('prometheus_vm' if prom.has_vm else 'prometheus_strings', prom)

    rxor = detect_rolling_xor(code)
    if rxor is not None:
        return ('rolling_xor_table', rxor)

    prng = detect_seeded_prng_cipher(code)
    if prng is not None:
        return ('seeded_prng_cipher', prng)

    carr = detect_constant_array(code)
    if carr is not None:
        return ('constant_array', carr)

    return ('math_expression', None)


def deobfuscate_lua(code: str, algo: str = 'auto', pretty: bool = True) -> str:
    """Deobfuscate code by detecting and unwrapping obfuscation layers."""
    unwrapped_layers: list[str] = []
    cur_code = code

    if algo == 'auto':
        for _ in range(5):
            inner, desc = try_unwrap_loadstring_packer(cur_code)
            if inner is None:
                break
            unwrapped_layers.append(f'loadstring_packer ({desc})')
            cur_code = inner
        detected_algo, meta = detect_obfuscator(cur_code)
    elif algo == 'loadstring_packer':
        inner, desc = try_unwrap_loadstring_packer(cur_code)
        if inner is None:
            raise ValueError("Input does not match `loadstring_packer` pattern.")
        unwrapped_layers.append(f'loadstring_packer ({desc})')
        return fold_lua_code(inner, unescape_strings=True, pretty=pretty)
    else:
        detected_algo = algo
        if algo in ('prometheus_vm', 'prometheus_strings'):
            meta = detect_prometheus(cur_code)
            if meta is None:
                raise ValueError(f"Input does not match `{algo}` pattern.")
        elif algo == 'rolling_xor_table':
            meta = detect_rolling_xor(cur_code)
            if meta is None:
                raise ValueError("Input does not match `rolling_xor_table` pattern.")
        elif algo == 'seeded_prng_cipher':
            meta = detect_seeded_prng_cipher(cur_code) or ('luau_randomseed', None)
        elif algo == 'constant_array':
            meta = detect_constant_array(cur_code)
            if meta is None:
                raise ValueError("Input does not match `constant_array` pattern.")
        else:
            meta = None

    if detected_algo == 'prometheus_vm':
        out = decode_prometheus_vm(cur_code, meta, pretty=pretty)  # type: ignore[arg-type]
    elif detected_algo == 'prometheus_strings':
        out = decode_prometheus_strings(cur_code, meta, pretty=pretty)  # type: ignore[arg-type]
    elif detected_algo == 'rolling_xor_table':
        out = decode_rolling_xor(cur_code, meta, pretty=pretty)  # type: ignore[arg-type]
    elif detected_algo == 'seeded_prng_cipher':
        out = decode_seeded_prng_cipher(cur_code, meta, pretty=pretty)  # type: ignore[arg-type]
    elif detected_algo == 'constant_array':
        out = decode_constant_array(cur_code, meta, pretty=pretty)  # type: ignore[arg-type]
    else:
        folded = fold_lua_code(cur_code, unescape_strings=True, pretty=pretty)
        out = (
            '-- Obfuscator: Math / Bit32 / String Expression & Escape Obfuscation\n\n'
            + folded
        )

    if unwrapped_layers:
        chain_hdr = '-- Unwrapped Outer Layers : ' + ' -> '.join(unwrapped_layers) + f' -> {detected_algo}\n'
        out = chain_hdr + out

    return out


def describe_detection(code: str) -> str:
    """Return a human-readable multi-layer detection summary without emitting full output."""
    lines: list[str] = []
    cur = code
    layer = 1
    for _ in range(5):
        inner, desc = try_unwrap_loadstring_packer(cur)
        if inner is None:
            break
        lines.append(f'Layer {layer}: loadstring_packer — {desc}')
        cur = inner
        layer += 1

    algo, meta = detect_obfuscator(cur)
    if algo in ('prometheus_vm', 'prometheus_strings') and isinstance(meta, PrometheusLayer1):
        lines.append(
            f'Layer {layer}: {algo} — table={meta.tbl_var} ({len(meta.strings_bytes)-1} strings), '
            f'idx_fn={meta.idx_fn}, offset={meta.offset}, has_vm={meta.has_vm}'
        )
    elif algo == 'rolling_xor_table' and isinstance(meta, RollingXorInfo):
        lines.append(
            f'Layer {layer}: rolling_xor_table — table={meta.tbl_var} ({len(meta.strings)-1} strings), '
            f'K={meta.key_k}, formula=(K + {meta.coef_i}*i + {meta.coef_j}*j) % 256'
        )
    elif algo == 'seeded_prng_cipher' and isinstance(meta, tuple):
        lines.append(f'Layer {layer}: seeded_prng_cipher — mode={meta[0]}, params={meta[1]}')
    elif algo == 'constant_array' and isinstance(meta, ConstantArrayInfo):
        lines.append(
            f'Layer {layer}: constant_array — table={meta.tbl_var} ({len(meta.strings)-1} strings), '
            f'idx_fn={meta.idx_fn}, offset={meta.offset}'
        )
    else:
        lines.append(f'Layer {layer}: math_expression — universal math/bit32/string constant folder')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description='All-in-One Automatic Lua / Luau Deobfuscator (auto-detects obfuscator & switches algorithm).'
    )
    parser.add_argument('input', type=Path, help='Path to obfuscated input .lua file')
    parser.add_argument('output', type=Path, nargs='?', default=None, help='Optional path to write deobfuscated .lua')
    parser.add_argument(
        '-a', '--algo',
        choices=SUPPORTED_ALGOS,
        default='auto',
        help='Obfuscator algorithm to use (default: auto-detect)',
    )
    parser.add_argument(
        '--no-format',
        action='store_true',
        help='Disable automatic Lua indentation/pretty-printing',
    )
    parser.add_argument(
        '--detect-only',
        action='store_true',
        help='Only fingerprint and print the detected obfuscator layer(s)',
    )

    args = parser.parse_args(argv)
    raw_bytes = args.input.read_bytes()
    try:
        code = raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        code = raw_bytes.decode('latin1')

    if args.detect_only:
        print(describe_detection(code))
        return

    summary = describe_detection(code)
    result = deobfuscate_lua(code, algo=args.algo, pretty=not args.no_format)

    if args.output:
        args.output.write_text(result, encoding='utf-8')
        print(summary)
        print(f'Saved deobfuscated output to: {args.output}')
    else:
        print(result)


if __name__ == '__main__':
    main()
