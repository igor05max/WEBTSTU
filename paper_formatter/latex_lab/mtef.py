"""Strict reader for the Equation 3 MTEF subset in native journal articles.

Binary grammar verified against LibreOffice's MathType importer (record/header
layout) and Wiris' MTEF documentation. This independent reader intentionally
rejects unsupported versions, embellishments and templates instead of guessing.
It reads an OLE compound stream as data; it never activates an OLE server.
"""
from io import BytesIO
import re
import struct


class UnsupportedEquation(ValueError):
    pass


SYMBOLS = {
    '−': '-', '×': r'\times ', '·': r'\cdot ', '⋅': r'\cdot ',
    '±': r'\pm ', '≤': r'\leq ', '≥': r'\geq ', '≠': r'\neq ',
    '∞': r'\infty ', '∂': r'\partial ', '∇': r'\nabla ',
    'α': r'\alpha ', 'β': r'\beta ', 'γ': r'\gamma ', 'δ': r'\delta ',
    'ε': r'\epsilon ', 'λ': r'\lambda ', 'θ': r'\theta ', 'μ': r'\mu ',
    'ν': r'\nu ', 'π': r'\pi ', 'ρ': r'\rho ', 'σ': r'\sigma ',
    'τ': r'\tau ', 'φ': r'\phi ', 'ω': r'\omega ', 'Δ': r'\Delta ',
    'Σ': r'\Sigma ', 'Ω': r'\Omega ', '°': r'{}^{\circ}',
    '\ueb04': r'\,', '\ueb05': r'\;', '\ueb02': r'\!',
}


class Reader:
    def __init__(self, data):
        if len(data) > 1024 * 1024:
            raise UnsupportedEquation('Equation stream too large.')
        self.data, self.pos, self.records = data, 0, 0

    def take(self, count):
        if self.pos + count > len(self.data):
            raise UnsupportedEquation('Truncated equation record.')
        result = self.data[self.pos:self.pos+count]; self.pos += count
        return result

    def byte(self):
        return self.take(1)[0]

    def sequence(self, depth=0):
        if depth > 64:
            raise UnsupportedEquation('Equation nesting limit.')
        nodes = []
        while True:
            tag = self.byte(); kind, flags = tag & 15, tag & 240
            self.records += 1
            if self.records > 20000:
                raise UnsupportedEquation('Equation record limit.')
            if kind == 0:
                if flags:
                    raise UnsupportedEquation('Unknown END flags.')
                return nodes
            if flags & 128:
                x, y = self.take(2)
                if x == 128 and y == 128:
                    self.take(4)
                flags &= ~128  # position nudges do not alter mathematical meaning
            if kind == 1:
                if flags not in {0, 16}:
                    raise UnsupportedEquation('Unsupported LINE flags.')
                nodes.append(('line', [] if flags & 16 else self.sequence(depth+1)))
            elif kind == 2:
                if flags not in {0, 16}:
                    raise UnsupportedEquation('Character embellishments need a verified adapter.')
                face = self.byte() - 128
                char = chr(int.from_bytes(self.take(2), 'little'))
                nodes.append(('char', face, char))
            elif kind == 3:
                if flags:
                    raise UnsupportedEquation('Unsupported template flags.')
                selector, variation, option = self.take(3)
                if option != 0:
                    raise UnsupportedEquation('Unsupported template options.')
                nodes.append(('template', selector, variation, self.sequence(depth+1)))
            elif kind in {10, 11, 12, 13, 14} and not flags:
                continue  # structural depth determines LaTeX script sizes
            elif kind == 9 and not flags:
                size = self.byte()
                self.take(2 if size == 101 else 3 if size == 100 else 1)
            else:
                raise UnsupportedEquation(f'Unsupported MTEF3 record {kind}/{flags}.')


def _tex(nodes):
    result = []
    for node in nodes:
        if node[0] == 'line':
            result.append(_tex(node[1]))
        elif node[0] == 'char':
            _, face, char = node
            if face == 22:
                raise UnsupportedEquation('Expanding delimiter outside its template.')
            if char in SYMBOLS:
                result.append(SYMBOLS[char])
            elif char in '=+-/(),.[]0123456789':
                result.append(char)
            elif char.isascii() and char.isalpha() and face in {1, 2, 3, 7, 8}:
                result.append((r'\mathrm{' + char + '}') if face in {1, 2} else
                              (r'\mathbf{' + char + '}') if face == 7 else char)
            elif char == ' ':
                result.append(r'\ ')
            else:
                raise UnsupportedEquation(f'Unmapped mathematical character U+{ord(char):04X}, face {face}.')
        else:
            _, selector, variation, children = node
            slots = [_tex(n[1]) for n in children if n[0] == 'line']
            # Delimiter glyphs duplicate the fence shape; validate then consume.
            extras = [n for n in children if n[0] != 'line']
            if selector in {0, 1, 2, 3, 4, 5}:
                left, right = [(r'\langle', r'\rangle'), ('(', ')'), (r'\{', r'\}'), ('[', ']'), ('|', '|'), (r'\Vert', r'\Vert')][selector]
                expected_glyphs = {'(', ')'} if selector == 1 else set()
                if variation != 0 or len(slots) != 1 or any(n[0] != 'char' or n[1] != 22 or n[2] not in expected_glyphs for n in extras):
                    raise UnsupportedEquation('Unsupported fence variation.')
                result.append(r'\left' + left + slots[0] + r'\right' + right)
            elif selector == 14 and variation == 0 and len(slots) == 2 and not extras:
                result.append(r'\frac{' + slots[0] + '}{' + slots[1] + '}')
            elif selector == 15 and variation in {0, 1, 2} and len(slots) == 2 and not extras:
                if variation == 0 and slots[0] or variation == 1 and slots[1]:
                    raise UnsupportedEquation('Inconsistent script slots.')
                if slots[0]: result.append('_{' + slots[0] + '}')
                if slots[1]: result.append('^{' + slots[1] + '}')
            else:
                raise UnsupportedEquation(f'Unsupported MTEF3 template {selector}/{variation}.')
    value = ''.join(result)
    for function in ('sin', 'cos', 'tan', 'log', 'ln', 'exp'):
        value = value.replace(''.join(r'\mathrm{' + c + '}' for c in function), '\\' + function + ' ')
    return value


def native_stream_to_latex(stream):
    if len(stream) < 33:
        raise UnsupportedEquation('Truncated Equation Native header.')
    header_length = struct.unpack_from('<I', stream)[0]
    data_length = struct.unpack_from('<I', stream, 8)[0]
    if header_length != 28 or header_length + data_length != len(stream):
        raise UnsupportedEquation('Unexpected Equation Native stream size.')
    reader = Reader(stream[header_length:])
    version, platform, product, major, minor = reader.take(5)
    if version != 3 or platform not in {0, 1}:
        raise UnsupportedEquation(f'Unsupported MTEF version {version}.')
    nodes = reader.sequence()
    if reader.pos != len(reader.data):
        raise UnsupportedEquation('Trailing equation records.')
    tex = _tex(nodes)
    if not tex.strip():
        raise UnsupportedEquation('Empty equation.')
    return tex


def ole_to_latex(payload):
    import olefile
    with olefile.OleFileIO(BytesIO(payload)) as compound:
        if not compound.exists('Equation Native'):
            raise UnsupportedEquation('OLE object has no Equation Native stream.')
        stream = compound.openstream('Equation Native').read()
    return native_stream_to_latex(stream)
