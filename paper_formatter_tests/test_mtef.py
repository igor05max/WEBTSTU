import struct
import unittest

from paper_formatter.latex_lab.mtef import native_stream_to_latex, UnsupportedEquation


def stream(hex_data):
    data = bytes.fromhex(hex_data)
    header = bytearray(28)
    struct.pack_into('<I', header, 0, 28)
    struct.pack_into('<I', header, 8, len(data))
    return bytes(header) + data


class MathTypeTests(unittest.TestCase):
    def test_verified_equation3_fraction_and_subscripts(self):
        source = stream('030101030a0a0112834e0002863d00030e00000112834c00030f01000b0112836300001100000a0112836400030f01000b0102883000028830000288320000110000000a02862b00028831000000')
        self.assertEqual(native_stream_to_latex(source), r'N=\frac{L_{c}}{d_{002}}+1')

    def test_greek_and_function_keep_mathematical_structure(self):
        source = stream('030101030a0a0112836400030f01000b010288300002883000028832000011000a02863d00030e0000010284bb03000102883200128273001282690012826e000284b80300000000')
        self.assertEqual(native_stream_to_latex(source), r'd_{002}=\frac{\lambda }{2\sin \theta }')

    def test_unknown_record_version_and_truncation_are_not_guessed(self):
        for data in ('050101030a0a010000', '030101030a0a01ff0000', '030101030a0a010283'):
            with self.subTest(data=data), self.assertRaises(UnsupportedEquation):
                native_stream_to_latex(stream(data))

    def test_literal_tex_cannot_be_injected_as_a_math_character(self):
        with self.assertRaises(UnsupportedEquation):
            native_stream_to_latex(stream('030101030a0a0102835c000000'))

    def test_extra_bytes_do_not_hide_unparsed_equations(self):
        source = stream('030101030a0a01028361000000')
        with self.assertRaises(UnsupportedEquation):
            native_stream_to_latex(source + b'\x02\x83b\x00')
