import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RESERVED = {"Parameters", "Devices", "Images", "Settings"}      # injected into plugin.py by Domoticz


class PluginNamespaceTests(unittest.TestCase):
    def test_plugin_py_never_binds_a_name_domoticz_injects(self):
        tree = ast.parse(open(os.path.join(HERE, "..", "plugin.py")).read())
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        self.assertEqual(bound & RESERVED, set(), "plugin.py would be shadowed by Domoticz' injected globals")


if __name__ == "__main__":
    unittest.main()
