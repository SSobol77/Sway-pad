import unittest
from sway_pad.sway import SwayEditor

class TestSwayPad(unittest.TestCase):
    def test_basic(self):
        editor = SwayEditor()
        self.assertIsNotNone(editor)
