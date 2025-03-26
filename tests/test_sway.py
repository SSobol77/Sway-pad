import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from sway_pad.sway import SwayEditor, load_config, run_pylint_on_code
from curses import wrapper

class TestSwayPad(unittest.TestCase):

    def setUp(self):
        self.stdscr_mock = MagicMock()
        self.editor = SwayEditor(self.stdscr_mock)
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_file.write(b"print('Hello World')")
        self.temp_file.close()

    def tearDown(self):
        os.unlink(self.temp_file.name)

    def test_load_config(self):
        config = load_config()
        self.assertIn("colors", config)
        self.assertIn("python", config["supported_formats"])
        self.assertEqual(config["editor"]["tab_size"], 4)

    def test_run_pylint(self):
        code = "print('Hello')"
        result = run_pylint_on_code(code)
        self.assertIn("Missing parentheses", result)

        large_code = "a" * 100001
        result = run_pylint_on_code(large_code)
        self.assertEqual(result, "File is too large for pylint analysis")

    @patch('subprocess.run')
    def test_pylint_timeout(self, mock_subprocess):
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["pylint"], timeout=3)
        result = run_pylint_on_code("print('Test')")
        self.assertEqual(result, "Pylint: time limit exceeded.")

    def test_file_operations(self):
        self.editor.filename = self.temp_file.name
        self.editor.open_file()
        self.assertEqual(len(self.editor.text), 1)
        self.assertIn("Hello World", self.editor.text[0])

        self.editor.save_file()
        with open(self.temp_file.name, 'r') as f:
            content = f.read()
            self.assertEqual(content, "print('Hello World')\n")

    def test_cursor_movement(self):
        self.editor.text = ["Line1", "Line2", "Line3"]
        self.editor.cursor_y = 0
        self.editor.cursor_x = 5

        self.editor.handle_down()
        self.assertEqual(self.editor.cursor_y, 1)
        self.editor.handle_up()
        self.assertEqual(self.editor.cursor_y, 0)

        self.editor.handle_end()
        self.assertEqual(self.editor.cursor_x, 5)

        self.editor.handle_page_down()
        self.assertEqual(self.editor.cursor_y, 2)

    def test_input_handling(self):
        self.editor.handle_char_input(ord('a'))
        self.assertEqual(self.editor.text[0], 'a')

        self.editor.handle_enter()
        self.assertEqual(len(self.editor.text), 2)
        self.assertEqual(self.editor.cursor_y, 1)

        self.editor.handle_delete()
        self.assertEqual(len(self.editor.text[1]), 0)

    def test_syntax_highlighting(self):
        self.editor.filename = "test.py"
        highlighted = self.editor.apply_syntax_highlighting("print('Hello')", "python")
        self.assertIsInstance(highlighted, list)
        self.assertGreater(len(highlighted), 0)

    @patch('curses.newpad')
    def test_screen_redraw(self, mock_pad):
        self.editor.draw_screen()
        self.stdscr_mock.clear.assert_called()
        self.stdscr_mock.refresh.assert_called()

    def test_keybindings(self):
        self.assertIn("save_file", self.editor.keybindings)
        self.assertEqual(self.editor.keybindings["quit"], 17)

    def test_special_functions(self):
        self.editor.toggle_insert_mode()
        self.assertFalse(self.editor.insert_mode)

        matches = self.editor.search_text("Hello")
        self.assertEqual(len(matches), 0)

        self.editor.find_and_replace()
        # TODO: Checking for substitution requires more complex input emulation.

    @patch('builtins.input', return_value='y')
    def test_prompt(self, mock_input):
        result = self.editor.prompt("Test prompt")
        self.assertEqual(result, 'y')

    def test_bracket_matching(self):
        self.editor.text = ["(example"]
        self.editor.cursor_x = 1
        self.editor.cursor_y = 0
        self.editor.highlight_matching_brackets()
        # TODO: Color verification requires emulation of curses

    def test_edge_cases(self):
        # Checking the opening of a non-existent file
        invalid_file = "/nonexistent"
        self.editor.open_file()
        self.assertIn("File not found", self.editor.status_message)

        # Error checking the save
        with patch('builtins.open', side_effect=PermissionError):
            self.editor.save_file()
            self.assertIn("No write permissions", self.editor.status_message)

if __name__ == '__main__':
    unittest.main()
    

